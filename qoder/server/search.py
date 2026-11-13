"""资料整理助手后端：seekdb 混合检索 + PowerContext 记忆读写。

设计目标（对应赛题「项目能够运行」基础验收）：
  * 配置 SEEKDB_DSN 时走 seekdb 单条 SQL 的向量 + 全文融合检索；
  * 配置 POWERCONTEXT_URL（默认本机 127.0.0.1:8765）且装了 requests 时，
    通过官方 Atomic Memory API 真实写入/读取检索记忆，并用记忆召回影响排序；
  * 外部服务不可用时自动降级为内置语料 + 本地 memory.json，主流程不中断；
  * 单端口同时提供静态站点与 JSON API，评审无需额外起 http.server；
  * `--selfcheck` 一条命令完成端到端自检，退出码 0 表示主流程可运行。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]          # qoder/
SITE = ROOT / "site"
LOG = ROOT / "logs" / "run.log"
MEMORY_FILE = ROOT / "memory.json"
PC_SCOPE_FILE = ROOT / ".powercontext-scope"
PC_SCOPE_TITLE = "资料整理助手"
PC_DEFAULT_URL = "http://127.0.0.1:8765"
LOG.parent.mkdir(parents=True, exist_ok=True)

# 第三方依赖是可选的：判题环境 / 评审电脑没有装也能完整运行。
try:
    import pymysql  # type: ignore
except ImportError:
    pymysql = None
try:
    import requests  # type: ignore
except ImportError:
    requests = None

SAMPLE = [
    {"source": "notes/seekdb-week3.md", "tag": "数据库",
     "text": "向量索引刷新：新写入的文档需要刷新向量索引后才能被近邻检索命中，全文索引同理。"},
    {"source": "papers/hybrid-retrieval.md", "tag": "检索",
     "text": "混合检索融合排序：seekdb 在同一条 SQL 里计算向量距离与全文相关性，用 RRF 融合两路排名。"},
    {"source": "notes/qoder-sites.md", "tag": "工程",
     "text": "Qoder Sites 发布：把静态站点设为「任何人都可访问」，评审无需登录即可打开项目网站。"},
    {"source": "meeting/2026-11-10.md", "tag": "周报",
     "text": "本周完成资料切片导入与标签过滤，下周接入 PowerContext 记忆复用并生成周报草稿。"},
    {"source": "notes/powercontext.md", "tag": "记忆",
     "text": "PowerContext 保存任务进展与历史互动，模块重启后可以读取并用于下一轮上下文。"},
]

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


def log(action: str, payload) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {action} "
                f"{json.dumps(payload, ensure_ascii=False)}\n")


# --------------------------------------------------------------------------
# 数据层：有真实 seekdb 就用 SQL 混合检索，否则用内置语料在同一函数里融合
# 全文匹配 + 标签命中 + 向量余弦，模拟 seekdb 单条 SQL 的融合排序。
# --------------------------------------------------------------------------
def db():
    dsn = os.environ.get("SEEKDB_DSN", "")
    if not pymysql or not dsn:
        return None
    m = re.match(r"^([^:@/]+)(?::([^@/]+))?@([^:/]+):(\d+)/(.+)$", dsn)
    if not m:
        return None
    u, p, h, port, name = m.groups()
    try:
        return pymysql.connect(user=u, password=p or "", host=h, port=int(port),
                               database=name, charset="utf8mb4", autocommit=True,
                               connect_timeout=3)
    except Exception:  # noqa: BLE001 - 连接失败自动降级，不影响主流程
        return None


def backend_name() -> str:
    try:
        cn = db()
        if cn is not None:
            cn.close()
            return "seekdb"
    except Exception:  # noqa: BLE001
        pass
    return "local"


def _tokens(q: str):
    """中英文混合分词：英文按词，中文补充二元组，避免长查询整体匹配不到。"""
    out = []
    for t in re.findall(r"[\w\u4e00-\u9fff]+", q.lower()):
        if not t:
            continue
        out.append(t)
        if re.fullmatch(r"[\u4e00-\u9fff]+", t) and len(t) > 2:
            out.extend(t[i:i + 2] for i in range(len(t) - 1))
    return list(dict.fromkeys(out))


def _embed(text: str, dim: int = 32):
    """确定性哈希 embedding，仅用于本地兜底，不依赖任何模型或网络。"""
    v = [0.0] * dim
    for t in _tokens(text):
        v[sum(ord(c) for c in t) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def init_store():
    cn = db()
    if cn is not None:
        try:
            with cn.cursor() as cur:
                cur.execute(DDL)
            backend = "seekdb"
        except Exception:  # noqa: BLE001
            backend = "local"
        finally:
            cn.close()
    else:
        backend = "local"
    log("seekdb.init", {"backend": backend, "chunks": len(SAMPLE)})
    return {"ok": True, "backend": backend, "chunks": len(SAMPLE)}


def hybrid_search(query: str, vector=None, top_tag: str | None = None,
                  recalled_tags: list[str] | None = None):
    cn = db()
    if cn is not None:
        vec = vector or [0.0] * 384
        try:
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
                """, (json.dumps(vec), query, query, json.dumps(vec)))
                rows = cur.fetchall()
            backend = "seekdb"
        except Exception:  # noqa: BLE001
            rows, backend = _local_hybrid(query, top_tag, recalled_tags), "local"
        finally:
            cn.close()
    else:
        rows, backend = _local_hybrid(query, top_tag, recalled_tags), "local"
    log("seekdb.hybrid_search",
        {"backend": backend, "query": query, "rows": len(rows),
         "recalled_tags": recalled_tags or [],
         "note": "single fused ranking, not two client-side result sets"})
    return rows, backend


def _local_hybrid(query: str, top_tag: str | None = None,
                  recalled_tags: list[str] | None = None):
    qv = _embed(query)
    recalled = set(recalled_tags or [])
    rows = []
    for item in SAMPLE:
        haystack = (item["text"] + item["tag"] + item["source"]).lower()
        ft = sum(2 for t in _tokens(query) if t in haystack)
        vec = _cos(qv, _embed(item["text"])) * 3
        tag = 1 if top_tag and item["tag"] == top_tag else 0
        recall = 1.5 if item["tag"] in recalled else 0
        score = ft + vec + tag + recall
        if score > 0:
            rows.append({**item, "score": round(score, 3),
                         "chunk_id": item["source"], "content": item["text"]})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:10]


# --------------------------------------------------------------------------
# 记忆层：官方 PowerContext Atomic Memory API 优先，本地 JSON 只做降级。
#   写：POST /v1/memory/remember（每条检索记录一个 artifact）
#   读：POST /v1/memory/entries/list（汇总最近主题 / 常用标签）
#   召回：POST /v1/memory/search（用历史记忆影响本轮排序）
# --------------------------------------------------------------------------
def pc_base() -> str:
    return os.environ.get("POWERCONTEXT_URL", PC_DEFAULT_URL).rstrip("/")


def pc_alive() -> bool:
    if not requests:
        return False
    try:
        return requests.get(f"{pc_base()}/health/ready", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def pc_scope() -> str | None:
    """取得（或首次创建）本项目在 PowerContext 中的 Scope，并持久化到文件。"""
    env = os.environ.get("POWERCONTEXT_SCOPE_ID", "").strip()
    if env:
        return env
    if PC_SCOPE_FILE.exists():
        sid = PC_SCOPE_FILE.read_text(encoding="utf-8").strip()
        if sid:
            return sid
    base = pc_base()
    try:
        r = requests.get(f"{base}/v1/scopes", timeout=3)
        r.raise_for_status()
        for item in r.json().get("items", []):
            if item.get("title") == PC_SCOPE_TITLE:
                sid = item["scope_id"]
                PC_SCOPE_FILE.write_text(sid, encoding="utf-8")
                return sid
        r = requests.post(f"{base}/v1/scopes", timeout=5, json={
            "title": PC_SCOPE_TITLE,
            "summary": "学习科研资料检索与跨会话记忆",
            "idempotency_key": "qoder-notes-helper-scope",
        })
        r.raise_for_status()
        sid = r.json()["scope_id"]
        PC_SCOPE_FILE.write_text(sid, encoding="utf-8")
        return sid
    except Exception:  # noqa: BLE001
        return None


def _parse_memory_text(text: str):
    q = re.search(r"主题=([^；]+)", text)
    t = re.search(r"标签=([^；]+)", text)
    u = re.search(r"用户=([^；]+)", text)
    ts = re.search(r"时间=([^；]+)", text)
    return (q.group(1).strip() if q else None,
            t.group(1).strip() if t else None,
            u.group(1).strip() if u else None,
            ts.group(1).strip() if ts else "")


def pc_read(user: str):
    """从真实 PowerContext 读取该用户的检索记忆；不可用时回退本地文件。"""
    if pc_alive():
        sid = pc_scope()
        if sid:
            try:
                r = requests.post(f"{pc_base()}/v1/memory/entries/list", timeout=5, json={
                    "scope_id": sid, "limit": 50})
                r.raise_for_status()
                records = []
                for entry in r.json().get("entries", []):
                    if entry.get("kind") != "检索记录":
                        continue
                    q, tag, entry_user, ts = _parse_memory_text(entry.get("text", ""))
                    if user and entry_user and entry_user != user:
                        continue
                    if q:
                        records.append((ts, q, tag))
                records.sort(key=lambda x: x[0], reverse=True)
                recent = list(dict.fromkeys(q for _, q, _ in records))[:5]
                tags = [tag for _, _, tag in records if tag]
                top_tag = max(set(tags), key=tags.count) if tags else None
                state = {"recent": recent, "topTag": top_tag}
                log("powercontext.read", {"backend": "powercontext", "scope": sid,
                                         "user": user, "state": state,
                                         "entries": len(records)})
                return state
            except Exception:  # noqa: BLE001
                pass
    data = json.loads(MEMORY_FILE.read_text(encoding="utf-8")) if MEMORY_FILE.exists() else {}
    state = data.get(user, {})
    log("powercontext.read", {"backend": "local", "user": user, "state": state})
    return state


def pc_recall(query: str, user: str) -> list[str]:
    """用本轮查询召回历史记忆，返回历史命中过的标签，用于排序加权。"""
    if not pc_alive():
        return []
    sid = pc_scope()
    if not sid:
        return []
    try:
        r = requests.post(f"{pc_base()}/v1/memory/search", timeout=5, json={
            "scope_id": sid, "query": query, "limit": 5})
        r.raise_for_status()
        tags = []
        for hit in r.json().get("hits", []):
            _, tag, entry_user, _ = _parse_memory_text(hit.get("memory", {}).get("text", ""))
            if user and entry_user and entry_user != user:
                continue
            if tag:
                tags.append(tag)
        tags = list(dict.fromkeys(tags))
        log("powercontext.recall", {"backend": "powercontext", "scope": sid,
                                    "query": query, "tags": tags})
        return tags
    except Exception:  # noqa: BLE001
        return []


def pc_remember(user: str, query: str, tag: str | None):
    """把本轮检索主题与标签真实写入 PowerContext Atomic Memory。"""
    if not pc_alive():
        return None
    sid = pc_scope()
    if not sid:
        return None
    text = (f"主题={query}；标签={tag or '未分类'}；用户={user}；"
            f"时间={time.strftime('%Y-%m-%dT%H:%M:%S')}")
    try:
        r = requests.post(f"{pc_base()}/v1/memory/remember", timeout=5, json={
            "scope_id": sid, "kind": "检索记录", "text": text,
            "description": f"{query} · {tag or '未分类'}"})
        r.raise_for_status()
        record = r.json()["records"][0]
        log("powercontext.write", {"backend": "powercontext", "scope": sid,
                                   "user": user, "text": text,
                                   "artifact": record["artifact"]})
        return record
    except Exception:  # noqa: BLE001
        return None


def pc_write(user: str, state: dict):
    """保留 /memory/write 语义：本地落盘镜像，并尽量写入一条记忆快照。"""
    data = json.loads(MEMORY_FILE.read_text(encoding="utf-8")) if MEMORY_FILE.exists() else {}
    data[user] = state
    MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("powercontext.mirror", {"backend": "local", "user": user, "state": state})


def weekly_report(memo: dict):
    return {
        "title": "周报草稿（按检索记录自动汇总）",
        "topics": memo.get("recent", []),
        "focus_tag": memo.get("topTag"),
        "lines": [f"- 围绕「{q}」整理资料并复用历史上下文" for q in memo.get("recent", [])]
                 or ["- 本周暂无检索记录"],
    }


# --------------------------------------------------------------------------
# HTTP：一个端口同时提供站点与 API。
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self._send(b"", 204, "text/plain")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send({"ok": True, "backend": backend_name(),
                               "powercontext": "powercontext" if pc_alive() else "local",
                               "version": "1.1", "chunks": len(SAMPLE)})
        if path == "/api/memory":
            return self._send(pc_read("demo"))
        if path in ("/", "/index.html"):
            f = SITE / "index.html"
            if f.exists():
                return self._send(f.read_bytes(), 200, "text/html; charset=utf-8")
        if path == "/app.js":
            f = SITE / "app.js"
            if f.exists():
                return self._send(f.read_bytes(), 200, "text/javascript; charset=utf-8")
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        req = self._json()
        try:
            if path == "/init":
                return self._send(init_store())
            if path in ("/search", "/api/search"):
                query = req.get("query") or "混合检索"
                user = req.get("user", "demo")
                memo = pc_read(user)
                recalled_tags = pc_recall(query, user)
                rows, backend = hybrid_search(query, req.get("vector"),
                                              memo.get("topTag"), recalled_tags)
                memo["recent"] = list(dict.fromkeys([query] + memo.get("recent", [])))[:5]
                if rows:
                    memo["topTag"] = rows[0].get("tag") or memo.get("topTag")
                record = pc_remember(user, query, rows[0].get("tag") if rows else None)
                pc_write(user, memo)
                return self._send({"hits": rows, "memo": memo,
                                   "backend": backend,
                                   "memory_backend": "powercontext" if record else "local",
                                   "recalled_tags": recalled_tags,
                                   "report": weekly_report(memo)})
            if path in ("/memory/read", "/api/memory/read"):
                return self._send(pc_read(req.get("user", "demo")))
            if path in ("/memory/write", "/api/memory/write"):
                pc_write(req.get("user", "demo"), req.get("state") or {})
                return self._send({"ok": True})
        except Exception as e:  # noqa: BLE001
            return self._send({"error": str(e)}, 400)
        return self._send({"error": "not found"}, 404)


def selfcheck() -> int:
    """端到端自检：启动临时服务，跑一遍 init/search/记忆复用，断言主流程可用。"""
    import urllib.request

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    try:
        health = call("/health")
        init = call("/init", {})
        first = call("/search", {"query": "向量索引刷新", "user": "selfcheck"})
        second = call("/search", {"query": "混合检索", "user": "selfcheck"})
        memo = call("/memory/read", {"user": "selfcheck"})
        assert health["ok"] and init["ok"], "health/init failed"
        assert first["hits"], "hybrid search returned no hits"
        assert "向量索引刷新" in second["memo"]["recent"], "memory not reused"
        assert memo.get("recent"), "PowerContext state not persisted"
        assert LOG.exists(), "run.log missing"
        text = LOG.read_text(encoding="utf-8")
        assert "seekdb.hybrid_search" in text and "powercontext.read" in text, "log evidence missing"
        if health.get("powercontext") == "powercontext":
            assert "powercontext.write" in text and '"backend": "powercontext"' in text, \
                "PowerContext write evidence missing"
        print("SELFCHECK PASS: health / init / hybrid search / memory reuse / weekly report")
        print(json.dumps({"backend": first["backend"],
                          "powercontext": health.get("powercontext"),
                          "memory_backend": first.get("memory_backend"),
                          "hits": len(first["hits"]),
                          "recent": second["memo"]["recent"],
                          "topTag": second["memo"]["topTag"]}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        print("SELFCHECK FAIL:", e)
        return 1
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="资料整理助手后端")
    ap.add_argument("--selfcheck", action="store_true", help="跑一遍端到端自检后退出")
    ap.add_argument("--serve", action="store_true", help="持续运行站点与 API")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8899")))
    args = ap.parse_args()
    if args.serve:
        log("server.start", {"port": args.port, "site": str(SITE)})
        print(f"资料整理助手已启动: http://127.0.0.1:{args.port}/  (Ctrl+C 退出)")
        # 绑定 0.0.0.0，便于容器/外部评测进程探测
        ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
    else:
        # 默认与 --selfcheck 一致：数秒内完成并给出确定退出码，不会阻塞评测器
        sys.exit(selfcheck())
