"""PowerContext 任务状态：写入、重启后读取复用。

优先走 PowerContext HTTP API；服务不可用时使用等价的本地 JSON 落盘，
接口与返回结构保持一致，保证「模块重启后仍能读取并使用状态」可复现。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "state" / "powercontext.json"
LOG = ROOT / "logs" / "powercontext-state.log"


def _base():
    return os.environ.get("POWERCONTEXT_URL", "").rstrip("/")


def _record(action: str, payload: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {action} {json.dumps(payload, ensure_ascii=False)}\n")


def load(task_id: str):
    """读取任务状态；返回 None 表示尚无任何记录。"""
    if _base():
        try:
            r = requests.get(f"{_base()}/v1/context/{task_id}", timeout=5)
            if r.ok:
                data = r.json()
                _record("READ/remote", {"task_id": task_id, "state": data})
                return data
        except requests.RequestException:
            pass
    if LOCAL.exists():
        data = json.loads(LOCAL.read_text(encoding="utf-8")).get(task_id)
        _record("READ/local", {"task_id": task_id, "state": data})
        return data
    return None


def save(task_id: str, *, done=None, todo=None, verification=None, extra=None):
    prev = load(task_id) or {}
    state = {
        "task_id": task_id,
        "done": list(prev.get("done", [])) + list(done or []),
        "todo": list(todo if todo is not None else prev.get("todo", [])),
        "verification": verification if verification is not None else prev.get("verification"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "extra": {**prev.get("extra", {}), **(extra or {})},
    }
    if _base():
        try:
            requests.post(f"{_base()}/v1/context/{task_id}", json=state, timeout=5).raise_for_status()
            _record("WRITE/remote", state)
            return state
        except requests.RequestException:
            pass
    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    all_states = json.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else {}
    all_states[task_id] = state
    LOCAL.write_text(json.dumps(all_states, ensure_ascii=False, indent=2), encoding="utf-8")
    _record("WRITE/local", state)
    return state


def reset():
    if LOCAL.exists():
        LOCAL.unlink()
    _record("RESET", {})
