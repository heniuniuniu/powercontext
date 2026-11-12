# MiniMax 开发记录 — 任务 T1

- 任务：订单列表新增 `status` 筛选，保持原有分页与排序行为不变
- 接入：`agent/minimax_agent.py`，MiniMax Chat Completions HTTP API
  （`MINIMAX_BASE_URL` 默认 `https://api.minimax.chat/v1`，`MINIMAX_MODEL` 默认 `MiniMax-M2`）
- 密钥：通过 `MINIMAX_API_KEY` 注入，**不随仓库提交**

## 提供给模型的上下文

由 `python -m sdd_context.cli context --task T1` 生成，四段来源真实存在：

| 段 | 来源 | 关键内容 |
| --- | --- | --- |
| specs | `corpus/specs/order-filter.md` | SPEC-ORDER-014 v2：先过滤再分页、排序不变 |
| code | `corpus/code/filter.py` | `def list_orders`、分页与 `created_at` 排序 |
| tests | `corpus/tests/test_order_filter.py` | `test_status_filter`、`test_status_filter_before_pagination` + 3 个回归用例 |
| state | `state/powercontext.json` | 已完成 / 待办 / 验证结果（见 `powercontext-state.log`） |

检索由 seekdb 完成，SQL 见 `seekdb-queries.log`。提示词模板在
`agent/minimax_agent.py:PROMPT`，其中 `{context}` 是上述 JSON。

## 产出的补丁

`diff/order-filter.patch`：给 `list_orders` 增加 `status=None` 参数，先过滤再分页，
不改默认行为与排序。补丁内容与 `demo_app/filter.py` → `demo_app/filter_after.py`
的差异逐行一致。

## 代码修改过程与验证

1. 按生效规格增加可选 `status` 参数。
2. 过滤放在分页切片之前，保证分页窗口基于过滤后的结果集。
3. 保持 `created_at DESC, id ASC` 排序与越界返回空列表。
4. 运行 `python -m unittest discover -s tests -v`：
   - 修改前退出码 1（`Ran 5 tests, FAILED (errors=2)`，两条目标用例因
     `unexpected keyword argument 'status'` 失败，属功能缺失而非环境错误），日志 `test-before.log`；
   - 修改后退出码 0（`Ran 5 tests, OK`），功能与回归用例全绿，日志 `test-after.log`。
5. `sdd_context.cli verify` 把验证结果回写 PowerContext，重启后 `context` 能读到
   `state.verification.passed == true`。

## 关于本次提交的说明

仓库未附带真实 MiniMax 密钥，因此 `agent/minimax_agent.py` 在本机以**离线复核模式**
运行：跳过在线生成、归档既有补丁并写出本记录。要复现完整的在线生成过程，
设置 `MINIMAX_API_KEY` 后执行 `python -m agent.minimax_agent` 即可，
脚本会用同一份上下文调用模型并用返回的 diff 覆盖 `diff/order-filter.patch`。
其余四项技术证据（上下文、seekdb 语句、状态复用、前后测试）均为仓库内可复现产物。
