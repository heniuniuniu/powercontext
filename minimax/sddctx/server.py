"""sddctx HTTP API（可选入口）。

只依赖标准库，方便在没有额外 Web 框架的环境里启动。

Endpoints:
    GET  /health              健康检查
    GET  /v1/context?task=…   按任务检索并返回上下文
    POST /v1/state            保存任务状态
    GET  /v1/state            读取任务状态
"""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .assemble import build_bundle
from .config import Settings
from .models import TaskState
from .state import PowerContextStateStore
from .store import SeekdbStore


class _Handler(BaseHTTPRequestHandler):
    settings: Settings

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        print(f"[sddctx] {fmt % args}")

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                with SeekdbStore(self.settings) as store:
                    self._send(200, {"ok": True, "database": store.server_info()})
                return
            if parsed.path == "/v1/context":
                task = (query.get("task") or [""])[0]
                if not task:
                    self._send(400, {"error": "缺少 task 参数"})
                    return
                with SeekContext(self.settings) as store:
                    bundle = build_bundle(store, self.settings, task)
                    self._send(200, bundle.to_dict())
                return
            if parsed.path == "/v1/state":
                state, history = PowerContextStateStore(self.settings).load()
                self._send(
                    200,
                    {
                        "current": state.to_dict() if state else None,
                        "history_count": len(history),
                    },
                )
                return
            self._send(404, {"error": "not found", "path": parsed.path})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "请求体不是合法 JSON"})
            return
        try:
            if parsed.path == "/v1/state":
                state = TaskState.from_dict(payload)
                receipt = PowerContextStateStore(self.settings).save(state)
                self._send(200, {"ok": True, "receipt": receipt, "state": state.to_dict()})
                return
            self._send(404, {"error": "not found", "path": parsed.path})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})


class SeekContext:
    """``with`` 语法糖：打开数据库连接。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = SeekdbStore(settings)

    def __enter__(self) -> SeekdbStore:
        return self.store.connect()

    def __exit__(self, *exc: object) -> None:
        self.store.close()


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    """启动 HTTP API（阻塞）。"""
    handler = type("SddctxHandler", (_Handler,), {"settings": settings})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"[sddctx] HTTP API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sddctx] shutting down")
    finally:
        server.server_close()
