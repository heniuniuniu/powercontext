"""资料整理助手后端：seekdb 混合检索 + PowerContext 记忆读写。

只依赖标准库 + pymysql / requests。连接串用环境变量注入，仓库内不含真实凭据。
"""
from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pymysql
import requests

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "run.log"
LOG.parent.mkdir(exist_ok=True)


def log(action: str, payload):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {action} {json.dumps(payload, ensure_ascii=False)}\n")


def db():
    dsn = os.environ.get("SEEKDB_DSN", "")
    m = re.match(r"^([^:@/]+)(?::([^@/]+))?@([^:/]+):(\d+)/(.+)$", dsn)
    if not m:
        raise RuntimeError("SEEKDB_DSN 应为 user[:pwd]@host:port/db")
    u, p, h, port, name = m.groups()
    return pymysql.connect(user=u, password=p or "", host=h, port=int(port),
                           database=name, charset="utf8mb4", autocommit=True)


DDL = """
CREATE TABLE IF NOT EXISTS kb_chunk (
  chunk_id VARCHAR(64) PRIMARY KEY,
  source VARCHAR(512), tag VARCHAR(64), content TEXT,
  embedding VECTOR(384),
  FULLTEXT KEY ft_content (content),
  VECTOR INDEX idx_vec (embedding) WITH (distance=L2),
  KEY idx_tag (tag)
)
"""


def hybrid_search(query: str, vector: list[float]):
    """向量 + 全文 + 标签在 seekdb 单条 SQL 内融合，不在客户端拼合两路结果。"""
    cn = db()
    with cn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("""
            SELECT chunk_id, source, tag, content,
                   L2_DISTANCE(embedding, %s) AS vec_dist,
                   MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score
              FROM kb_chunk
             WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) > 0
                OR APPROXIMATE VECTOR(embedding, %s) WITHIN TOP 10
             ORDER BY ft_score DESC, vec_dist ASC
             LIMIT 10
        """, (json.dumps(vector), query, query, json.dumps(vector)))
        rows = cur.fetchall()
    cn.close()
    log("seekdb.hybrid_search", {"query": query, "rows": len(rows)})
    return rows


def pc_read(user: str):
    base = os.environ.get("POWERCONTEXT_URL", "").rstrip("/")
    if not base:
        state = json.loads((ROOT / "memory.json").read_text(encoding="utf-8")) if (ROOT / "memory.json").exists() else {}
        return state.get(user, {})
    r = requests.get(f"{base}/v1/context/{user}", timeout=5)
    r.raise_for_status()
    return r.json()


def pc_write(user: str, state: dict):
    base = os.environ.get("POWERCONTEXT_URL", "").rstrip("/")
    if not base:
        all_state = json.loads((ROOT / "memory.json").read_text(encoding="utf-8")) if (ROOT / "memory.json").exists() else {}
        all_state[user] = state
        (ROOT / "memory.json").write_text(json.dumps(all_state, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        requests.post(f"{base}/v1/context/{user}", json=state, timeout=5).raise_for_status()
    log("powercontext.write", {"user": user, "state": state})


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/init":
                cn = db()
                with cn.cursor() as cur:
                    cur.execute(DDL)
                cn.close()
                return self._send({"ok": True})
            if self.path == "/search":
                query = req["query"]
                # 真实部署时向量由本地 embedding 模型生成；此处接收前端传入，服务端不做推理
                rows = hybrid_search(query, req.get("vector") or [0.0] * 384)
                user = req.get("user", "demo")
                memo = pc_read(user)
                memo["recent"] = list(dict.fromkeys([query] + memo.get("recent", [])))[:5]
                if rows:
                    memo["topTag"] = rows[0]["tag"]
                pc_write(user, memo)
                return self._send({"hits": rows, "memo": memo})
        except Exception as e:  # noqa: BLE001
            return self._send({"error": str(e)}, 400)
        self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8899), Handler).serve_forever()
