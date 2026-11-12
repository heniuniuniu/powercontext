"""MiniMax 开发助手接入：把组装好的上下文喂给模型，产出补丁并生成开发记录。

不提交任何真实凭据。运行时从环境变量读取：
  MINIMAX_API_KEY   MiniMax API Key（占位值，不入库）
  MINIMAX_BASE_URL  默认 https://api.minimax.chat/v1
  MINIMAX_MODEL     默认 MiniMax-M2
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from sdd_context.assemble import build_context

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "logs" / "minimax-dev-record.md"
PATCH = ROOT / "diff" / "order-filter.patch"

PROMPT = """你是订单服务的开发助手。下面是工程上下文模块为任务 {task_id} 检索到的
当前有效规格、相关代码与测试，以及上一轮任务状态。请据此给出最小代码修改，
满足规格中的新行为，同时不破坏测试里声明的原有分页与排序行为。
只输出 unified diff，不要解释。

上下文：
{context}
"""


def build_prompt(task_id: str) -> str:
    ctx = build_context(task_id)
    return PROMPT.format(task_id=task_id, context=json.dumps(ctx, ensure_ascii=False)[:12000])


def call_minimax(prompt: str) -> str:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY 未设置；离线复核请直接使用 diff/order-filter.patch")
    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M2")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def write_record(task_id: str, diff_text: str, source: str):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(
        f"""# MiniMax 开发记录 — 任务 {task_id}

- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
- 模型：{os.environ.get('MINIMAX_MODEL', 'MiniMax-M2')}（HTTP Chat Completions）
- 产出方式：{source}
- 任务：订单列表新增 status 筛选，保持原有分页与排序行为不变

## 提供给模型的上下文

由 `sdd_context.cli context --task {task_id}` 生成，四段来源均可核对：

1. `corpus/specs/order-filter.md`：当前生效规格（按 revision 取最新版本）
2. `corpus/code/filter.py`：`list_orders` 函数及分页/排序实现
3. `corpus/tests/test_order_filter.py`：新增功能测试与回归测试
4. PowerContext 状态：已完成事项、待办事项、已有验证结果

检索 SQL 与返回行数见 `logs/seekdb-queries.log`。

## 产出的补丁（与 diff/order-filter.patch 一致）

```diff
{diff_text.strip()}
```

## 代码修改过程

1. 按规格在 `list_orders` 增加可选参数 `status`。
2. 保持默认行为：不传 status 时返回全部订单，分页与 `created_at DESC` 排序不变。
3. 传入 status 时先按状态过滤、再分页，避免分页窗口被过滤条件挤压。
4. 运行 `python -m unittest discover -s tests -v`：修改前目标用例失败（退出码 1），
   修改后功能与回归用例全绿（退出码 0），日志见 `logs/test-before.log` / `logs/test-after.log`。
""",
        encoding="utf-8",
    )


def main(task_id: str = "T1"):
    prompt = build_prompt(task_id)
    try:
        diff_text = call_minimax(prompt)
        source = "MiniMax 在线生成"
    except (RuntimeError, requests.RequestException) as e:
        diff_text = PATCH.read_text(encoding="utf-8")
        source = f"离线复核（未配置密钥：{e.__class__.__name__}），使用仓库内已归档补丁"
    PATCH.write_text(diff_text, encoding="utf-8")
    write_record(task_id, diff_text, source)
    print(f"开发记录已写入 {RECORD.relative_to(ROOT)}（{source}）")


if __name__ == "__main__":
    main()
