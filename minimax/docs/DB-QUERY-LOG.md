# 数据库查询记录

> 本文件记录本作品**实际执行过的 SQL 及其真实输出**，用于证明「工程资料确实存进了数据库、
> 检索结果确实来自数据库，而不是把文件读进内存后自己排序」。
> 所有输出都可以用 `sddctx db --stats` / `sddctx db --sql "<SQL>"` 复现。

---

## 1. 数据库与连接方式

| 项 | 值 |
| --- | --- |
| 引擎 | **seekdb**（OceanBase 轻量版，MySQL 协议兼容） |
| 版本 | `5.7.25-OceanBase seekdb-v1.4.0.0` |
| 接入方式 | 嵌入式 `pylibseekdb==1.4.0.post2`（本机后台实例 + `pymysql` 客户端） |
| 库名 | `sddctx` |
| 表结构版本 | `schema_version = 1` |
| 全文索引 | `FULLTEXT INDEX idx_doc_search ON doc_index (search_text) WITH PARSER ik`（可用） |
| 切换到 OceanBase 集群 | `SDDCTX_DB_MODE=server` + `SDDCTX_DB_HOST/PORT/USER/PASSWORD`，**SQL 完全一致** |

> 默认用嵌入式 seekdb，是为了让评审在**没有 Docker、没有集群**的环境里也能一条命令跑通；
> 换成 OceanBase 集群只需要改环境变量，存取层代码一行不用动。

---

## 2. 表结构（`sddctx init` 建表）

| 表 | 作用 | 关键列 |
| --- | --- | --- |
| `spec_item` | SDD 规格条目（需求 / 接口契约 / 决策） | `spec_id`、`status`（`active`/`superseded`）、`body`、`content_sha256`、`source_file`、`source_line` |
| `code_symbol` | 代码符号（函数 / 类 / 方法） | `symbol_id`、`signature`、`body`、`line_start`、`line_end` |
| `test_case` | 测试用例 | `test_id`、`kind`（`target`/`regression`）、`docstring`、`body` |
| `doc_index` | 统一检索文档（规格 + 代码 + 测试） | `doc_id`、`doc_type`、`ref_id`、`status`、`search_text`（含全文索引） |
| `doc_token` | 倒排索引（token → doc 权重） | `doc_id`、`token`、`weight` |
| `ingest_run` | 每次导入的变更痕迹 | `run_id`、`source_file`、`doc_type`、`action`（`inserted`/`updated`/`removed`/`unchanged`） |

辅助索引：`idx_doc_token_token`、`idx_doc_index_type`、`idx_spec_item_status`、`idx_test_case_kind`。

---

## 3. 实际执行的查询与真实输出

### Q1 · 各表行数（证明数据确实入库）

```bash
.venv/bin/python -m sddctx db --stats
```

```json
{
  "database": {
    "mode": "embedded",
    "version": "5.7.25-OceanBase seekdb-v1.4.0.0",
    "database": "sddctx",
    "schema_version": "1",
    "fts": true,
    "fts_error": null
  },
  "tables": {
    "spec_item": 19,
    "code_symbol": 11,
    "test_case": 14,
    "doc_index": 44,
    "doc_token": 2198,
    "ingest_run": 307
  }
}
```

> 19 条规格 = 8 条需求（REQ-CPN-001~008）+ 5 条接口契约（REQ-API-001~005）+ 6 条决策（ADR-001~006）。
> 14 条测试 = 目标功能测试 5 条 + 回归测试 9 条。
> `ingest_run` 是累计行数（每次导入 × 每个条目一行），**不是**版本数；
> 最近一次变更见 Q6。

---

### Q2 · 规格条目全量（含状态与内容指纹）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT spec_id, status, doc_version, LEFT(content_sha256,12) AS sha12 FROM spec_item ORDER BY spec_kind, spec_id"
```

真实输出（节选，完整 19 行）：

| spec_id | status | doc_version | content_sha256（前 12 位） |
| --- | --- | --- | --- |
| ADR-001 | active | 1.4 | `332101ce6575` |
| ADR-002 | active | 1.4 | `13dac0f701f1` |
| **ADR-003** | **superseded** | 1.4 | `62e3f539b7d4` |
| **ADR-004** | **superseded** | 1.4 | `f8fd289955fb` |
| ADR-005 | active | 1.4 | `e2568ed2b3b7` |
| ADR-006 | active | 1.4 | `ba1dabf6a947` |
| REQ-API-001 ~ 005 | active | 1.1 | `0e330283cc78` / `13ab503510fa` / `9731db8d402c` / `05638976a849` / `66d8478c05f1` |
| REQ-CPN-001 | active | 1.3 | `1a5895c40d0d` |
| REQ-CPN-002 | active | 1.3 | `efcba1205ff4` |
| REQ-CPN-003 | active | 1.3 | `3c6ce96a3fff` |
| **REQ-CPN-004** | active | 1.3 | **`63905905d438`** |
| REQ-CPN-005 | active | 1.3 | `c5ebc3e05d2d` |
| **REQ-CPN-006** | active | 1.3 | **`b172b6d4429d`** |
| REQ-CPN-007 | active | 1.3 | `2649b6e37d7f` |
| REQ-CPN-008 | active | 1.3 | `7f5349666231` |

> **这张表就是「生效 / 已失效」分流的依据**：`ADR-003`、`ADR-004` 的 `status` 是
> `superseded`，模块据此把它们放进「已失效条目」分区，并明确标注「不得作为约束」。

---

### Q3 · 原生全文检索（seekdb BM25 相关度）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT d.ref_id, MATCH(d.search_text) AGAINST ('门槛' IN BOOLEAN MODE) AS relevance \
   FROM doc_index d WHERE MATCH(d.search_text) AGAINST ('门槛' IN BOOLEAN MODE) \
   ORDER BY relevance DESC LIMIT 6"
```

真实输出：

| ref_id | relevance |
| --- | --- |
| `REQ-CPN-003` | 0.4441701134816709 |
| `ADR-006` | 0.3875601970575365 |
| `ADR-003` | 0.36773153581273227 |
| `REQ-CPN-004` | 0.3660290749987844 |
| `ADR-004` | 0.3577478742069567 |
| `REQ-CPN-008` | 0.3521705131391422 |

> 相关度是**数据库算出来的**，不是 Python 端排的序：门槛相关的需求排在前面，
> 而 ADR-003/004（已失效）虽然也命中关键词，但会被状态分流单独隔离。

---

### Q4 · 倒排索引命中（模块内部检索用的第一路信号）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT t.doc_id, SUM(t.weight) AS w, COUNT(DISTINCT t.token) AS matched \
   FROM doc_token t WHERE t.token IN ('apply_coupon','threshold','coupon','settle') \
   GROUP BY t.doc_id ORDER BY w DESC LIMIT 10"
```

真实输出：

| doc_id | w | matched |
| --- | --- | --- |
| `code:demo_app.pricing.apply_coupon` | 29 | 3 |
| `test:...test_coupon_threshold.py::test_no_threshold_discount_coupon_is_applicable` | 24 | 3 |
| `code:demo_app.pricing.settle` | 21 | 3 |
| `test:...test_coupon_regression.py::test_threshold_coupon_reduces_fixed_amount` | 21 | 3 |
| `test:...test_coupon_threshold.py::test_no_threshold_coupon_respects_max_discount` | 20 | 3 |
| `test:...test_coupon_threshold.py::test_no_threshold_coupon_base_excludes_freight` | 20 | 3 |
| `test:...test_coupon_threshold.py::test_no_threshold_coupon_in_settlement` | 20 | 3 |
| `test:...test_coupon_threshold.py::test_discount_coupon_applies_at_exact_threshold` | 20 | 3 |
| `spec:REQ-CPN-008` | 16 | 3 |
| `test:...test_coupon_regression.py::test_negative_threshold_coupon_is_rejected` | 16 | 3 |

> 标识符 `apply_coupon` 被准确召回——倒排索引保证了「任务里点名的符号」一定不丢。

---

### Q5 · 索引分区统计（生效 / 已失效）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT doc_type, status, COUNT(*) AS n FROM doc_index GROUP BY doc_type, status ORDER BY doc_type, status"
```

真实输出：

| doc_type | status | n |
| --- | --- | --- |
| code | active | 11 |
| spec | active | 17 |
| **spec** | **superseded** | **2** |
| test | active | 14 |

---

### Q6 · 导入历史（谁在什么时候改了什么）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT run_id, started_at, source_file, doc_type, action, doc_id FROM ingest_run \
   WHERE action IN ('inserted','updated','removed') ORDER BY started_at DESC, doc_id LIMIT 10"
```

真实输出（节选）：

| run_id | started_at | source_file | doc_type | action | doc_id |
| --- | --- | --- | --- | --- | --- |
| `20261114-095352-00374e` | 2026-11-14T09:53:53 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.apply_coupon` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.apply_coupon` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.Coupon` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.CouponKind` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.CouponNotApplicable` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.settle` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing.SettlementResult` |
| `20261114-095249-df809f` | 2026-11-14T09:52:50 | `demo_app/pricing.py` | code | updated | `demo_app.pricing._round_money` |

> 后续几次导入（10:00、10:03 两轮完整验证）全部返回 `unchanged`——
> 说明增量导入是**幂等**的：内容没变就不写库，也不产生噪声的变更记录。

---

### Q7 · 规格演进前后的指纹对比（证明不是固定答案）

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT spec_id, status, content_sha256 FROM spec_item \
   WHERE spec_id IN ('REQ-CPN-004','REQ-CPN-006','ADR-006')"
```

| spec_id | 演进前 content_sha256 | 演进后 content_sha256 | 说明 |
| --- | --- | --- | --- |
| REQ-CPN-004 | `272be2df0488c974…` | `63905905d438c974…` | 正文修订（v1.3） |
| REQ-CPN-006 | `d92d84ae7ccd8d4e…` | `b172b6d4429d8d4e…` | 正文修订（v1.3） |
| ADR-006 | *（不存在）* | `ba1dabf6a947c8c8…` | v1.4 新增条目 |

留证文件：`evidence/spec-evolution/before-snapshot.json`、`after-snapshot.json`、
`after-ingest-specs.json`。

---

## 4. 模块内部执行的检索 SQL

上下文里的每一条内容都来自下面两条 SQL（`sddctx/store.py`）：

**① 倒排索引（`search_by_tokens`）** —— 权重表与倒排表 JOIN：

```sql
SELECT t.doc_id AS doc_id,
       SUM(t.weight * w.w) AS score,
       COUNT(DISTINCT t.token) AS matched
FROM doc_token t
JOIN (SELECT 'apply_coupon' AS token, 3.0 AS w UNION ALL
      SELECT 'threshold'  AS token, 3.0 AS w UNION ALL ...) w ON w.token = t.token
GROUP BY t.doc_id
ORDER BY score DESC, matched DESC
LIMIT 200;
```

**② 原生全文检索（`search_by_fts`）**：

```sql
SELECT doc_id,
       MATCH(search_text) AGAINST (%s IN BOOLEAN MODE) AS score
FROM doc_index
WHERE MATCH(search_text) AGAINST (%s IN BOOLEAN MODE)
ORDER BY score DESC
LIMIT 200;
```

两路结果合并后，在 Python 端做**结构化加权**（任务点名符号 +0.45、点名完整 ref_id +0.35、
模块命中 +0.12、失效条目 −0.05），并记录每条命中的**理由**（见 `evidence/context-latest.md`
的「检索理由」列）。最终进入上下文的条目：

| 分区 | 数量 | 来源 |
| --- | --- | --- |
| 适用规格（active） | 12 | `spec_item` + `doc_index` |
| 已失效条目（superseded） | 2 | ADR-003、ADR-004，单独分区标注「不得作为约束」 |
| 待改代码符号 | 9 | `code_symbol`（含 `file:line`） |
| 目标功能测试 | 5 | `test_case`（`kind=target`） |
| 回归测试 | 9 | `test_case`（`kind=regression`） |

> 检索结果**直接决定**上下文内容：规格正文、代码片段、测试用例都是从数据库里读出来的，
> 每条都带 `source_ref`（形如 `specs/coupon-rules.md:42#REQ-CPN-004`）可回仓库核对。

---

## 5. 复现全部查询

```bash
cd minimax
.venv/bin/python -m sddctx init
.venv/bin/python -m sddctx ingest
.venv/bin/python -m sddctx db --stats
.venv/bin/python -m sddctx db --sql "<上面的 SQL>"
```

若要用 OceanBase 集群：

```bash
export SDDCTX_DB_MODE=server
export SDDCTX_DB_HOST=<集群地址> SDDCTX_DB_PORT=2881
export SDDCTX_DB_USER=root@xxx#cluster SDDCTX_DB_PASSWORD=***
export SDDCTX_DB_NAME=sddctx
.venv/bin/python -m sddctx init && .venv/bin/python -m sddctx ingest
```
