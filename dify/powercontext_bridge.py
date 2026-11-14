#!/usr/bin/env python3
"""PowerContext 记忆桥：给 Dify 的 HTTP Request 节点提供两个稳定端点。

GET  /memory/read?player=<昵称>          -> 取回该玩家往期的「梗」
POST /memory/write {player,topic,content} -> 把本轮串词与新梗写回

默认走 PowerContext HTTP API；没有配置 API 时退化为本地 JSON 文件，
保证评审在只导入 DSL 的情况下也能把整个流程跑通（退化模式会在响应里标注）。
配置示例使用占位值，不提交任何真实凭据。
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PC_API_BASE = os.getenv("PC_API_BASE", "").rstrip("/")
PC_API_KEY = os.getenv("PC_API_KEY", "")
PC_READ_PATH = os.getenv("PC_READ_PATH", "/v1/memories/search")
PC_WRITE_PATH = os.getenv("PC_WRITE_PATH", "/v1/memories")
PC_TIMEOUT = float(os.getenv("PC_TIMEOUT", "8"))
LOCAL_DB = Path(os.getenv("PC_LOCAL_DB", Path(__file__).with_name("memory-store.json")))
PORT = int(os.getenv("PC_BRIDGE_PORT", "8765"))


def _local_read(player):
    if not LOCAL_DB.exists():
        return []
    try:
        data = json.loads(LOCAL_DB.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if x.get("player") == player][-5:]


def _local_write(record):
    data = []
    if LOCAL_DB.exists():
        try:
            data = json.loads(LOCAL_DB.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
    data.append(record)
    LOCAL_DB.write_text(json.dumps(data[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def _pc_request(method, path, payload=None):
    url = PC_API_BASE + path
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if PC_API_KEY:
        req.add_header("Authorization", "Bearer " + PC_API_KEY)
    with urllib.request.urlopen(req, timeout=PC_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_memory(player):
    """返回给 LLM 节点看的纯文本，避免把 PowerContext 的原始结构暴露给提示词。"""
    if PC_API_BASE:
        try:
            raw = _pc_request("POST", PC_READ_PATH, {"query": f"player:{player}", "top_k": 5})
            items = raw.get("results", raw.get("memories", raw.get("data", [])))
            lines = []
            for it in items:
                text = it.get("content", it.get("text", "")) if isinstance(it, dict) else str(it)
                if text:
                    lines.append("- " + str(text)[:300])
            return {"source": "powercontext", "memories": lines}
        except (urllib.error.URLError, ValueError, OSError) as exc:
            return {"source": "powercontext-error-fallback", "error": str(exc),
                    "memories": [x["content"] for x in _local_read(player)]}
    return {"source": "local-fallback",
            "memories": [x["content"] for x in _local_read(player)]}


def write_memory(record):
    record = {"player": str(record.get("player", "anonymous")),
              "topic": str(record.get("topic", "")),
              "content": str(record.get("content", ""))[:2000]}
    if PC_API_BASE:
        try:
            _pc_request("POST", PC_WRITE_PATH, {
                "content": record["content"],
                "metadata": {"player": record["player"], "topic": record["topic"]},
            })
            _local_write(record)  # 本地留一份，便于评审核对读写记录
            return {"source": "powercontext", "ok": True}
        except (urllib.error.URLError, ValueError, OSError) as exc:
            _local_write(record)
            return {"source": "powercontext-error-fallback", "ok": True, "error": str(exc)}
    _local_write(record)
    return {"source": "local-fallback", "ok": True}


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._send({"ok": True,
                               "powercontext_configured": bool(PC_API_BASE),
                               "store": str(LOCAL_DB)})
        if parsed.path == "/memory/read":
            qs = urllib.parse.parse_qs(parsed.query)
            player = (qs.get("player") or ["anonymous"])[0]
            result = read_memory(player)
            # Dify 的 {{#read_memory.body#}} 直接拿到文本，串词节点里更好用
            return self._send({"player": player,
                               "source": result["source"],
                               "memory_text": "\n".join(result["memories"]) or "（这位玩家还没有往期记录）",
                               "memories": result["memories"]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/memory/write":
            return self._send({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            return self._send({"error": "invalid json"}, 400)
        return self._send(write_memory(payload))

    def do_OPTIONS(self):
        self._send({})

    def log_message(self, fmt, *args):
        print("[pc-bridge] " + fmt % args, flush=True)


if __name__ == "__main__":
    LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    print(f"[pc-bridge] listening on 0.0.0.0:{PORT}; PowerContext={'configured' if PC_API_BASE else 'local fallback'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
