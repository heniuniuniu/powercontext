---
name: sdd-context-manager
description: 面向软件持续演进的 SDD 工程上下文模块。根据开发任务从 seekdb 检索当前有效规格、相关代码与测试，组装上下文交给 MiniMax 完成代码修改，并用 PowerContext 保存/复用任务状态。当需要判断「这次任务该遵守什么要求、改哪些文件、怎么验证」时使用。
---

# SDD 工程上下文模块（MiniMax × seekdb × PowerContext）

## 模块结构

```
sdd_context/
  store.py     seekdb 资料层：建表、全文索引、导入、按任务检索
  state.py     PowerContext 任务状态：写入、重启后读取复用
  assemble.py  上下文组装：规格 / 代码 / 测试 / 状态四段，均带真实来源
  cli.py       调用入口：init / import / context / save / verify
agent/minimax_agent.py  MiniMax 补丁生成 + 开发记录
```

调用入口是 CLI（也可 `from sdd_context.assemble import build_context` 直接调用）：
输入 `task_id`，输出 JSON，字段 `specs`、`code`、`tests`、`state`，每项含 `path`、
`lines`、`revision` 与 `text`。输入/输出样例见 `verification/验证样例.md`。

## 上下文选择方式

1. `import` 把 `corpus/` 下的规格、源码、测试按文件切片写入 seekdb 表 `sdd_asset`，
   记录 `kind`、`path`、`revision`、`valid_from` 和正文；对正文建全文索引。
2. `context --task T1` 用任务描述生成检索词，在 seekdb 中执行
   `MATCH(content) AGAINST(... IN BOOLEAN MODE)` 分别取规格/代码/测试各 Top-K；
   规格按 `valid_from DESC` 过滤出**当前生效**版本，失效规格不进入上下文。
3. 把 `state.py` 读回的上一轮「已完成 / 待办 / 验证结果」并入上下文，
   避免重复劳动。
4. 更新规格文件后重新 `import`，检索命中新 `revision`，输出随之变化 —— 不是固定答案。

检索 SQL 与返回值原文记录在 `logs/seekdb-queries.log`，可逐条核对。

## 各项技术的分工

| 技术 | 在流程中的实际作用 | 核对材料 |
| --- | --- | --- |
| MiniMax | 接收组装好的上下文，产出 `diff/order-filter.patch` | `logs/minimax-dev-record.md` |
| seekdb | 保存资料/索引；检索结果直接构成上下文的 specs/code/tests 三段 | `logs/seekdb-queries.log` |
| PowerContext | 保存任务进展与测试结果；模块重启后读取，参与下一轮上下文 | `logs/powercontext-state.log` |

## 验证流程（六项必做对应的证据）

- **能够运行**：`README.md` 的 5 条命令，从空库开始，不依赖未交付的本地状态。
- **MiniMax 实际参与**：开发记录写明任务、喂给模型的上下文摘要、产出的补丁；
  补丁与提交的 `diff/order-filter.patch` 逐行对应。
- **上下文正确**：`verification/验证样例.md` 列出必须出现的规格条目、代码符号和测试名；
  并给出「改规格 → 重新 import → 输出变化」的复核步骤。
- **数据库实际参与**：`logs/seekdb-queries.log` 含建表、导入、检索的 SQL 与返回行数。
- **状态能够复用**：`logs/powercontext-state.log` 记录写入、清空进程内存、重启后读取，
  以及状态被用于后续 `context` 输出的片段。
- **代码修改有效**：`logs/test-before.log`（目标用例失败、退出码 1，属功能缺失而非环境错误）
  与 `logs/test-after.log`（功能 + 回归全绿、退出码 0），测试集前后一致。

## 重置方法

`python -m sdd_context.cli init --reset` 清空表与索引，`rm -rf state/` 清空任务状态，
再按 README 从头执行即可复现。
