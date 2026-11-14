# PowerContext 状态写入与复用记录

> 本文件记录任务进展**真实写入 PowerContext**、并在模块重启后**读取复用**的全过程。
> 配套：`docs/RUNBOOK.md`（运行说明）、`docs/VERIFICATION.md`（验证样例）。

---

## 1. 接入方式

| 项 | 值 |
| --- | --- |
| 组件 | PowerContext **1.5.1**（PyPI `powercontext[client,server]`） |
| Server | `powercontext server run --env-file powercontext.env`，监听 `http://127.0.0.1:8000` |
| 后端 | SQLite（`./.sddctx/powercontext/powercontext.db`），随 Server 持久化 |
| 客户端 | **Python Client SDK**（`powercontext.client.PowerContextClient`），非手写 HTTP |
| 用到的接口 | `create_scope` / `remember_memory` / `list_memory_entries` / `search_memory` / `prepare_context` |
| 配置 | `powercontext.env`（只有本地路径与开关，**不含任何凭据**） |

就绪检查：

```bash
curl -s http://127.0.0.1:8000/health/ready
```

```json
{"status":"ready","checks":{"runtime":"ready","database":"ready","artifact_processing_supervisor":"disabled",
 "access_mode":"disabled","authentication_provider":"disabled","access_provider":"disabled",
 "access_resource_kinds":"server,scope,artifact",
 "access_artifact_families":"atomic-memory:enabled,experience:enabled,handoff:enabled,memory:enabled,
 "profile:enabled,prompt:enabled,skill:enabled"}}
```

---

## 2. Scope：逻辑名 → 服务端分配的不透明 ID

| 项 | 值 |
| --- | --- |
| 逻辑 Scope（模块内使用） | `project:sddctx-demo` |
| 幂等键 | `sddctx:project:sddctx-demo` |
| 服务端分配的 Scope ID | **`scp_2s0282qdy9j7c1k8f55h701x92`** |
| 本地缓存 | `.sddctx/powercontext-scope.json` |

创建流程（`sddctx/state.py::_ensure_scope`）：

1. 先用逻辑名向服务端 `get_scope` 探测；
2. 探测不到就用缓存的 Scope ID 再试一次；
3. 仍拿不到才 `create_scope(idempotency_key="sddctx:project:sddctx-demo")`，
   服务端返回不透明 ID，随后缓存到 `.sddctx/powercontext-scope.json`。

> 幂等键保证**重复运行不会产生第二个 Scope**；缓存保证后续进程直接复用同一个 Scope。
> 逻辑名与真实 Scope ID 的映射由服务端管理，模块不依赖任何本地假设。

---

## 3. 写入：追加式（append-only）状态记录

每次保存任务状态，都**追加一条 Memory**（`kind = sdd-task-state`），内容带自增 `seq`：

```text
SDDCTX-STATE {"done_items": [...], "marker": "SDDCTX-STATE", "notes": [], "seq": 1,
 "status": "in_progress", "task_id": "task-coupon-threshold", "task_text": "...",
 "todo_items": [...], "updated_at": "2026-11-14T10:03:57", "validation_results": []}
```

恢复时读出全部记录并取 `seq` 最大的一条作为当前状态——
**不依赖搜索引擎的排序行为**，跨进程、跨会话都稳定，同时保留完整变更历史。

本轮（`scripts/verify.py` 从**空状态**开始）实际写入 4 条：

| seq | 时间 | 状态 | 内容摘要 | artifact_id | revision |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-14T10:03:57 | `in_progress` | 已导入规格/代码/测试；已按任务组装上下文；待办 3 项 | `art_580d966b211c4c85b938d0881503ccd6` | 1 |
| 2 | 2026-11-14T10:04:14 | `in_progress` | 同上（Agent 步骤开始前再次落盘） | `art_4bedc08aa4564619a2347dc39dedc51b` | 1 |
| 3 | 2026-11-14T10:04:26 | `done` | 已产出代码修改 `demo_app/pricing.py`；目标/回归测试均通过 | `art_cdb3a63d8ded4316aef81cfb5b3ce4ad` | 1 |
| 4 | 2026-11-14T10:04:31 | `done` | 已保存并在新进程中恢复；MiniMax 已完成代码修改；两项验证结果 | `art_6923fd75382e419c93ecbf0321833114` | 1 |

写入凭证（`sddctx state save` 的输出）：

```json
{
  "seq": 4,
  "scope_id": "scp_2s0282qdy9j7c1k8f55h701x92",
  "kind": "sdd-task-state",
  "citation": null,
  "entry_id": null,
  "task_id": "task-coupon-threshold",
  "server": "http://127.0.0.1:8000",
  "logical_scope": "project:sddctx-demo"
}
```

---

## 4. 复用：**新进程**读回状态

### 4.1 `sddctx state show`（独立进程，与写入进程不同）

```bash
.venv/bin/python -m sddctx state show
```

```json
{
  "scope_id": "scp_2s0282qdy9j7c1k8f55h701x92",
  "logical_scope": "project:sddctx-demo",
  "server": "http://127.0.0.1:8000",
  "current": {
    "task_id": "task-coupon-threshold",
    "status": "done",
    "done_items": ["已导入规格/代码/测试到 seekdb", "已按任务组装上下文",
                   "已保存任务状态并在新进程中恢复", "MiniMax 已完成代码修改"],
    "todo_items": [],
    "validation_results": ["目标功能测试：通过（exit_code=0）", "回归测试：通过（exit_code=0）"],
    "updated_at": "2026-11-14T10:04:31"
  },
  "history_count": 4,
  "history": [ "…seq 1…4，含 artifact_id 与 revision…" ]
}
```

> **关键点**：这是**另一个进程**（`python -m sddctx state show`）读出来的，
> 证明状态落在 PowerContext 的持久化存储里，而不是某个进程的内存里。
> `verify.py` 的 `state-restart-read` 步骤把这一条作为**硬性校验**（不通过则整体失败）。

### 4.2 状态被并入新上下文

```bash
.venv/bin/python -m sddctx query --with-state "<任务>"
```

生成的上下文 Markdown（`evidence/context-latest.md` 第 293 行起）包含：

```markdown
## 任务进展（来自 PowerContext 恢复）

- 任务 ID：task-coupon-threshold
- 状态：in_progress
- 已完成：
  - 已导入规格/代码/测试到 seekdb
  - 已按任务组装上下文
- 待办：
  - 由 MiniMax 完成代码修改
  - 运行目标功能测试
  - 运行回归测试
```

> 恢复出的状态被**真正用于**后续上下文构造：交给 MiniMax 的那份上下文里，
> 助手能直接看到「已经做到哪、还剩什么」，不必从头再来。
> `verify.py` 的 `state-reused-in-context` 步骤校验 `markdown_contains_state = true`。

### 4.3 语义召回（跨会话检索历史状态）

```bash
.venv/bin/python -m sddctx state search "无门槛券 门槛判定"
```

```json
{
  "scope_id": "scp_2s0282qdy9j7c1k8f55h701x92",
  "query": "无门槛券 门槛判定",
  "hit_count": 4,
  "hits": [
    {"score": 0.01639344262295082, "state": {"seq": 2, "status": "in_progress", "…": "…"}},
    {"score": 0.016129032258064516, "state": {"seq": 1, "status": "in_progress", "…": "…"}},
    {"score": 0.015873015873015872, "state": {"seq": 3, "status": "done", "…": "…"}},
    {"score": 0.015625, "state": {"seq": 4, "status": "done", "…": "…"}}
  ]
}
```

> `search_memory` 用于**跨会话召回**（谁在别的任务里做过类似的事），
> `list_memory_entries` 用于**权威全量读取**（恢复当前状态）。
> 两条通道都实测可用：前者按语义排序，后者按 `seq` 定序。

---

## 5. 与其他技术的衔接

| 环节 | PowerContext 承担的部分 |
| --- | --- |
| 任务开始 | 记录「已导入资料、已组装上下文」，待办清单落盘 |
| 交给 MiniMax 前 | 把恢复的状态写进上下文 Prompt，模型据此接着干 |
| 改完代码 | 记录产物路径与测试结果（`validation_results`） |
| 任务结束 | `status = done`，`todo_items` 清空，验证结果随状态一起留存 |
| 下一次运行 | 新进程读回 `current`，并入新上下文；`history` 保留全部历史版本 |

---

## 6. 复现

```bash
cd minimax
# ① 启动 PowerContext（另开终端）
.venv/bin/powercontext server run --env-file powercontext.env

# ② 从空状态保存
.venv/bin/python -m sddctx state save \
  --task-id task-coupon-threshold \
  --task-text "支持无门槛优惠券 threshold=0，门槛判定使用 >=" \
  --status in_progress \
  --done "已导入规格/代码/测试到 seekdb" \
  --todo "由 MiniMax 完成代码修改"

# ③ 新进程读取（模块重启后）
.venv/bin/python -m sddctx state show
.venv/bin/python -m sddctx query --with-state "支持无门槛优惠券 threshold=0，门槛判定使用 >="

# ④ 语义召回
.venv/bin/python -m sddctx state search "无门槛券 门槛判定"
```

### 重置为「空状态」

```bash
# 停掉 PowerContext Server 后：
rm -rf .sddctx/powercontext      # 删除 SQLite 状态库
# 重新启动 Server，下一次 state save 就是 seq=1
```

重置后 `sddctx state show` 返回 `current: null`、`history_count: 0`。
本作品提交的这一轮证据，就是按上面的方式**从空状态**重新跑出来的
（`verify-latest.json` 中 `state-save` 步骤的 `previous_state_existed: false`）。
