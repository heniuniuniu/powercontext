"""PowerContext 适配层：保存任务进展，并在模块重启后读取复用。

接入方式：**Python Client SDK**（``powercontext.client.PowerContextClient``），
由 ``powercontext server run`` 提供的本地 Server 承载持久化。

设计要点：
* 状态以追加（append-only）方式写入 Memory，``kind = sdd-task-state``；
* 每条记录带自增 ``seq`` 与时间戳，重新读取时取 ``seq`` 最大的一条作为当前状态；
* 这样既保留了变更历史，又不依赖检索排序，跨进程、跨会话都能稳定恢复；
* Scope 是服务端分配的不透明 ID，首次使用时按固定的幂等键创建并缓存到本地，
  之后所有读写都复用同一个 Scope。
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import pathlib
import re
from typing import Any

from .config import Settings
from .models import TaskState

STATE_KIND = "sdd-task-state"
_MARKER = "SDDCTX-STATE"
_SCOPE_CACHE = "powercontext-scope.json"


def _encode(state: TaskState, seq: int) -> str:
    payload = state.to_dict()
    payload["seq"] = seq
    payload["marker"] = _MARKER
    return f"{_MARKER} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"


def _decode(text: str) -> dict[str, Any] | None:
    if _MARKER not in (text or ""):
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if data.get("marker") != _MARKER:
        return None
    return data


class PowerContextStateStore:
    """基于 PowerContext 的任务状态存储。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    def _client(self):  # type: ignore[no-untyped-def]
        from powercontext.client import PowerContextClient

        return PowerContextClient(self.settings.powercontext_url)

    def _scope_cache_path(self) -> pathlib.Path:
        return self.settings.state_dir / _SCOPE_CACHE

    def _cached_scope(self) -> str | None:
        path = self._scope_cache_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        scope_id = data.get("scope_id")
        return str(scope_id) if scope_id else None

    def _cache_scope(self, scope_id: str) -> None:
        path = self._scope_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "scope_id": scope_id,
                    "logical_scope": self.settings.powercontext_scope,
                    "server": self.settings.powercontext_url,
                    "cached_at": _dt.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def _ensure_scope(self) -> str:
        """解析（或创建）本作品使用的 Scope，返回服务端分配的不透明 Scope ID。"""
        from powercontext.http import CreateScopeRequest

        configured = self.settings.powercontext_scope
        cached = self._cached_scope()
        async with self._client() as client:
            for candidate in (configured, cached):
                if not candidate:
                    continue
                try:
                    await client.get_scope(candidate)
                    return candidate
                except Exception:  # noqa: BLE001
                    continue
            descriptor = await client.create_scope(
                CreateScopeRequest(
                    title=f"sddctx · {configured}",
                    summary="SDD 上下文模块的任务状态作用域",
                    idempotency_key=f"sddctx:{configured}",
                )
            )
        self._cache_scope(descriptor.scope_id)
        return descriptor.scope_id

    # ------------------------------------------------------------------
    async def _save(self, scope_id: str, state: TaskState) -> dict[str, Any]:
        from powercontext.http import RememberMemoryRequest

        seq = await self._next_seq(scope_id)
        encoded = _encode(state, seq)
        async with self._client() as client:
            response = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope_id,
                    kind=STATE_KIND,
                    text=encoded,
                    reason=f"sddctx task {state.task_id} seq={seq}",
                )
            )
        return {
            "seq": seq,
            "scope_id": scope_id,
            "kind": STATE_KIND,
            "citation": getattr(response, "citation", None),
            "entry_id": getattr(response, "entry_id", None),
        }

    async def _load(self, scope_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """读取本模块写入的全部状态记录（``list_memory_entries`` 返回权威全量文本）。"""
        from powercontext.http import ListMemoryEntriesRequest

        async with self._client() as client:
            response = await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True, limit=100)
            )
        decoded: list[dict[str, Any]] = []
        for entry in getattr(response, "entries", []) or []:
            if getattr(entry, "kind", None) != STATE_KIND:
                continue
            payload = _decode(getattr(entry, "text", "") or "")
            if not payload:
                continue
            artifact = getattr(entry, "artifact", None)
            decoded.append(
                {
                    "payload": payload,
                    "artifact_id": getattr(artifact, "artifact_id", None),
                    "revision": getattr(artifact, "revision", None),
                    "memory_state": str(getattr(entry, "state", "")),
                }
            )
        if not decoded:
            return None, []
        decoded.sort(key=lambda item: int(item["payload"].get("seq", 0)))
        best = decoded[-1]
        return best["payload"], decoded

    async def _search(self, scope_id: str, query: str) -> list[dict[str, Any]]:
        """语义检索（``search_memory``），用于跨会话召回。"""
        from powercontext.http import SearchMemoryRequest

        async with self._client() as client:
            result = await client.search_memory(
                SearchMemoryRequest(scope_id=scope_id, query=query)
            )
        found: list[dict[str, Any]] = []
        for hit in getattr(result, "hits", []) or []:
            record = getattr(hit, "memory", None)
            if record is None:
                continue
            payload = _decode(getattr(record, "text", "") or "")
            if payload:
                found.append(
                    {
                        "payload": payload,
                        "score": float(getattr(hit, "score", 0.0) or 0.0),
                        "kind": getattr(record, "kind", None),
                    }
                )
        found.sort(key=lambda item: -item["score"])
        return found

    async def _next_seq(self, scope_id: str) -> int:
        payload, _ = await self._load(scope_id)
        if not payload:
            return 1
        return int(payload.get("seq", 0)) + 1

    async def _prepare(self, scope_id: str, query: str) -> str:
        from powercontext.http import PrepareContextRequest

        async with self._client() as client:
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query=query)
            )
        return getattr(prepared, "content", "") or ""

    # ------------------------------------------------------------------
    # 同步外观
    # ------------------------------------------------------------------
    def save(self, state: TaskState) -> dict[str, Any]:
        """保存任务状态，返回写入凭证。"""
        state.updated_at = _dt.datetime.now().isoformat(timespec="seconds")
        scope_id = asyncio.run(self._ensure_scope())
        result = asyncio.run(self._save(scope_id, state))
        result["task_id"] = state.task_id
        result["server"] = self.settings.powercontext_url
        result["logical_scope"] = self.settings.powercontext_scope
        return result

    def load(self) -> tuple[TaskState | None, list[dict[str, Any]]]:
        """读取当前任务状态与全部历史版本。"""
        scope_id = asyncio.run(self._ensure_scope())
        payload, history = asyncio.run(self._load(scope_id))
        if not payload:
            return None, []
        state = TaskState.from_dict(payload)
        return state, history

    def recall(self, query: str) -> str:
        """用 PowerContext 的 ``prepare_context`` 做一次上下文召回（可选增强）。"""
        scope_id = asyncio.run(self._ensure_scope())
        return asyncio.run(self._prepare(scope_id, query))

    def search(self, query: str) -> list[dict[str, Any]]:
        """用 PowerContext 的语义检索召回与查询相关的历史状态。"""
        scope_id = asyncio.run(self._ensure_scope())
        return asyncio.run(self._search(scope_id, query))

    def scope_id(self) -> str:
        """返回当前使用的 PowerContext Scope ID。"""
        return asyncio.run(self._ensure_scope())

    def available(self) -> bool:
        """探测 PowerContext Server 是否可达。"""
        try:
            self.load()
            return True
        except Exception:  # noqa: BLE001
            return False
