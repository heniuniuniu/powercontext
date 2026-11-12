"""上下文组装：把 seekdb 检索结果与 PowerContext 状态合成一份开发上下文。"""
from __future__ import annotations

import json

from . import state, store

TASKS = {
    "T1": {
        "title": "订单列表新增 status 筛选，保持原有分页与排序行为不变",
        "terms": "订单 status 筛选 分页 排序 filter order",
        "spec_needle": "status",
        "code_needle": "def list_orders",
        "test_needle": "test_status_filter",
    },
}


def build_context(task_id: str) -> dict:
    task = TASKS.get(task_id)
    if not task:
        raise KeyError(f"未知任务 {task_id}，可用: {list(TASKS)}")

    spec_rows = store.search(task["terms"], kind="spec", limit=3)
    code_rows = store.search(task["terms"], kind="code", limit=3)
    test_rows = store.search(task["terms"], kind="test", limit=3)

    # 规格按 revision 取当前生效版本，失效版本不进入上下文
    latest_by_path = {}
    for r in sorted(spec_rows, key=lambda x: (x["revision"], x["valid_from"])):
        latest_by_path[r["path"]] = r
    specs = [store.as_item(r, task["spec_needle"]) for r in latest_by_path.values()]

    saved = state.load(task_id)
    return {
        "task": {"id": task_id, "title": task["title"]},
        "specs": specs,
        "code": [store.as_item(r, task["code_needle"]) for r in code_rows],
        "tests": [store.as_item(r, task["test_needle"]) for r in test_rows],
        "state": saved or {"done": [], "todo": [], "verification": None, "note": "首次运行，尚无历史状态"},
        "sources": [r["path"] for r in spec_rows + code_rows + test_rows],
    }


def to_json(ctx: dict) -> str:
    return json.dumps(ctx, ensure_ascii=False, indent=2)
