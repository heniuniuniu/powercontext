# 一页技术说明

> 规有所依——面向软件持续演进的 SDD 上下文管理系统

## 问题

AI 开发助手改代码时，需要知道四件事：**当前该遵守哪些要求**、**要改哪些代码**、
**怎么验证**、**上次做到哪了**。资料分散、规格演进、任务中断，会让助手用到过时的约束、
漏掉既有行为，或者重复劳动。

## 模块结构

```
sddctx/                      上下文模块（Python 包，CLI + HTTP API）
├── config.py                配置：路径 / 数据库 / PowerContext / MiniMax
├── models.py                数据结构：SpecItem / CodeSymbol / TestCase / ContextBundle
├── tokenize.py              中英文混合分词（标识符 / 英文词 / 中文词组 / 中文二元组）
├── parsers.py               规格 Markdown、Python 符号（ast）、测试用例（ast）解析
├── store.py                 seekdb／OceanBase 存取层（建表 / 增量导入 / 检索 SQL）
├── retrieve.py              检索与打分：倒排索引 + 原生全文 + 结构化加权
├── assemble.py              上下文组装：生效规格 / 已失效条目 / 待改代码 / 目标与回归测试
├── state.py                 PowerContext 任务状态读写（跨会话恢复）
├── patching.py              函数级补丁：只替换目标函数，其余字节不变
├── minimax_bridge.py        MiniMax Messages API 调用与证据留存
├── server.py                HTTP API（标准库实现）
└── cli.py                   命令行入口

specs/                       SDD 规格（需求 + 接口契约 + 决策记录 ADR）
demo_app/                    被修改的业务工程（含目标功能测试与回归测试）
scripts/verify.py            端到端验证驱动
evidence/                    运行留证（可重新生成）
```

## 上下文是怎么选出来的

```
任务描述
   │
   ├─ 分词：标识符(3.0) > 英文词(2.0) > 中文词组(1.6) > 中文二元组(1.0)
   │
   ├─▶ ① seekdb 倒排索引（doc_token）：保证标识符一定被召回，可解释
   │      SELECT t.doc_id, SUM(t.weight*w.w) … JOIN (权重表) … GROUP BY t.doc_id
   │
   ├─▶ ② seekdb 原生全文检索（FULLTEXT INDEX … WITH PARSER ik）
   │      MATCH(search_text) AGAINST ('…' IN BOOLEAN MODE)
   │
   ├─▶ ③ 结构化加权：任务点名符号(+0.45) / 模块(+0.12) / 规格状态
   │
   ▼
上下文分组
   ├── 适用规格（status=active）      ← 必须遵守
   ├── 已失效条目（status=superseded）← 单独分区，明确标注「不得作为约束」
   ├── 待改代码（符号 + file:line）
   ├── 目标功能测试（kind=target）
   ├── 回归测试（kind=regression）    ← 整个测试文件补齐，避免「必须保持的行为」出现缺口
   └── 任务进展（来自 PowerContext 恢复）
```

**为什么必须区分「生效」与「已失效」**：`specs/decisions.md` 里 ADR-003（门槛用严格大于）
与 ADR-004（券必须配正门槛）都已被 REQ-CPN-003/004 取代。若助手读到它们，会把门槛改回旧行为。
模块因此按 `状态` 字段分流，并把失效条目放进独立分区。

## 三项技术的分工

| 技术 | 在本作品中的**实际作用** | 接入方式 | 版本 |
| --- | --- | --- | --- |
| **seekdb**（OceanBase） | 存规格、代码符号、测试用例与倒排/全文索引；**检索结果直接决定上下文里出现什么** | 嵌入式 `pylibseekdb`（本机后台进程 + MySQL 协议），SQL 走 `pymysql` | pylibseekdb 1.4.0.post2 / seekdb v1.4.0.0 |
| **PowerContext** | 保存任务进展（已完成/待办/验证结果），模块重启后读取并并入新上下文 | Python Client SDK + 本地 Server（`powercontext server run`） | powercontext 1.5.1 |
| **MiniMax** | 读上下文 → 产出函数补丁 → 驱动真实代码修改 | HTTP API，Anthropic Messages 协议 `POST {base}/messages` | MiniMax-M3.1-Flash |

三者都在同一条链路上，缺一不可：seekdb 决定**看什么**，MiniMax 决定**怎么改**，
PowerContext 决定**从哪儿接着干**。

## 两个关键设计

### 1. 函数级补丁：用结构保证最小改动

模型只返回**要改的函数**，模块用 `ast` 定位该函数在源文件中的行区间并原地替换，
文件其余部分**逐字节不变**。

```
<<<PATCH file=demo_app/pricing.py function=apply_coupon>>>
…该函数的新实现…
<<<END PATCH>>>
```

效果：本次任务的最终 diff 是 **2 行增、2 行删**，模型顺手重排 import、
改写 docstring 之类的问题从「靠提示词约束」变成「结构上不可能发生」。
落盘前还有 `ast.parse` 语法校验，语法不过直接拒绝写入；
写盘时保留原文件行尾风格，落盘后统计 `unchanged_bytes`（本次 **2859 字节逐字节未变**）。

### 2. 状态用追加方式存，恢复不依赖检索排序

每次写状态都追加一条 Memory（`kind = sdd-task-state`），内容带自增 `seq` 与时间戳。
恢复时读出全部记录并取 `seq` 最大的一条——不依赖搜索引擎的排序行为，
跨进程、跨会话都稳定；同时保留了完整变更历史。

## 一次完整闭环

```
① import      specs/ + demo_app/  ──▶  seekdb（spec_item / code_symbol / test_case / doc_index / doc_token）
② select      任务描述 ──▶ 检索 ──▶ 生效规格 + 已失效条目 + 待改代码 + 目标/回归测试
③ assist      上下文 + 任务 ──▶ MiniMax ──▶ 函数补丁
④ modify      补丁 ──▶ ast 校验 ──▶ 写回 demo_app/pricing.py（只动 2 行）
⑤ verify      目标功能测试 + 回归测试 ──▶ exit_code
⑥ persist     任务状态 ──▶ PowerContext ──▶ 重启后读取并并入下一次上下文
```

## 扩展能力（题目中的非必做项，本作品已实现）

* **规格变更处理**：重新导入按 `content_sha256` 判定 inserted / updated / removed，
  被删除的条目连同其倒排索引一并清理；输出随之更新，不返回固定答案。
  本作品实跑过一次真实演进（v1.2→v1.3 修订两条需求、v1.3→v1.4 新增 ADR-006），
  前后检索快照见 `evidence/spec-evolution/`。
* **跨会话任务恢复**：PowerContext 的 `list_memory_entries`（权威全量）与
  `search_memory`（语义召回）双通道，恢复出的状态直接进入下一次上下文。
* **成本/时延优化**：思维链预算限制（`thinking.budget_tokens`）+ `max_tokens` 耗尽自动重试，
  避免为一次 2 行改动烧掉上万 token。
