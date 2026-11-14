---
name: sdd-context
description: 在开始或继续一项开发任务前，先用 sddctx 从 seekdb／OceanBase 检索当前有效的规格、待改代码与验证测试，并从 PowerContext 恢复上次任务状态；改完代码后用 sddctx 跑测试并把结果写回 PowerContext。当任务涉及规格、接口契约、设计决策或需要跨会话续接时使用。
---

# 用 sddctx 获取工程上下文

本技能把 `sddctx` 上下文模块接入 AI 开发助手。**不要**凭记忆或凭通读代码去判断
「该遵守什么要求」，先让模块给出当前有效的上下文。

## 何时使用

- 收到一项开发任务，需要知道适用的规格、要改哪些符号、怎么验证；
- 规格或设计决策可能已经演进，需要区分「生效」与「已失效」；
- 任务被中断过（换会话、换机器、换 agent），需要接着上次继续。

## 前置

上下文模块与 PowerContext Server 需已启动（见 `docs/RUNBOOK.md`）。
如果 `sddctx doctor` 报告 PowerContext 不可达，**明确告知用户**状态无法持久化，
不要假装保存成功。

## 工作流程

### 1. 检索上下文

```bash
python -m sddctx query "<用户任务原文>"
```

输出是 Markdown，包含五块：

| 小节 | 含义 | 你必须怎么用 |
| --- | --- | --- |
| 适用规格条目（当前有效，必须遵守） | `status=active` 的需求/接口契约 | **当作硬约束**逐条对照 |
| 已失效条目（仅供追溯，**不得**作为约束） | `status=superseded` 的历史决策 | **不要**按它改代码；它只解释历史行为 |
| 需要修改的代码 | 符号 + `file:line` | 改动落在这些符号上 |
| 验证测试 | 目标功能测试 / 回归测试 | 回归测试是「必须保持的行为」清单 |

每条都带 `source_ref`（形如 `specs/coupon-rules.md:32#REQ-CPN-003`），
需要看原文时按这个定位去读，不要凭摘要猜测。

### 2. 恢复上次任务状态

```bash
python -m sddctx state show
```

若存在未完成的任务（`status=in_progress`），**先**按它的 `todo_items` 继续，
不要从头重做。若需要把状态并入上下文：

```bash
python -m sddctx query --with-state "<用户任务原文>"
```

### 3. 改代码

按上下文里的「适用规格」改；用「回归测试」清单自查有没有破坏既有行为。

### 4. 验证并回写状态

```bash
python -m sddctx test <测试路径> --label target
python -m sddctx state save --task-id <任务ID> \
  --status done --done "..." --todo "..." --result "目标测试：5 passed（exit_code=0）"
```

`state save` 必须带上真实的测试结论；**不要**在没跑测试时写「已完成」。

## 边界

- 上下文里的「已失效条目」不是约束。若某条已失效决策看起来仍该遵守，
  说明规格之间有冲突——**停下来问用户**，不要自行取舍。
- `sddctx` 只提供上下文与状态，不替你决定方案。
- 测试失败时，先看退出码是「断言失败」还是「环境错误」：环境错误（如缺依赖）
  不算功能未实现，不要据此宣称任务完成。
