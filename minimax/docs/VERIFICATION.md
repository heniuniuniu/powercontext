# 验证样例

> 配套运行说明：`docs/RUNBOOK.md`
> 一次性执行：`python scripts/verify.py`

---

## 1. 任务描述

**任务 ID**：`task-coupon-threshold`

> 支持无门槛优惠券（`coupon.threshold = 0`）：无门槛券不做门槛判定即可使用；
> 订单商品金额恰好等于券门槛时，折扣券也应当可以使用（门槛判定使用 `>=` 而不是 `>`）；
> 负门槛仍视为券配置错误。
> 请只修改 `apply_coupon` 的门槛判定逻辑，不要改变满减券/折扣券的减免计算、
> 上限封顶、运费隔离与金额精度等既有行为。

### 要改什么

`demo_app/pricing.py` 中 `apply_coupon(order, coupon)` 的**门槛判定**：

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| `threshold == 0`（无门槛券） | 抛 `CouponNotApplicable` | **可用**，按券规则减免 |
| `threshold > 0` 且 `subtotal == threshold` | 抛 `CouponNotApplicable` | **可用** |
| `threshold > 0` 且 `subtotal < threshold` | 抛 `CouponNotApplicable` | 抛 `CouponNotApplicable`（不变） |
| `threshold < 0`（配置错误） | 抛 `CouponNotApplicable` | 抛 `CouponNotApplicable`（不变） |

### 哪些原有行为必须保持

由 `demo_app/tests/test_coupon_regression.py` 的 9 条用例逐条锁定：

1. 未达门槛的订单不可用券（`test_discount_coupon_rejected_when_below_threshold`）
2. 满减券按固定金额减免（`test_threshold_coupon_reduces_fixed_amount`）
3. 优惠金额受 `max_discount` 封顶（`test_discount_coupon_respects_max_discount`）
4. 优惠金额不得超过商品小计（`test_discount_never_exceeds_subtotal`）
5. 运费不参与优惠计算（`test_freight_excluded_from_coupon_base`）
6. 应付金额包含运费（`test_settlement_total_includes_freight`）
7. 不使用优惠券时优惠金额为 0（`test_settlement_without_coupon`）
8. 异常信息包含券编号（`test_not_applicable_error_mentions_coupon_id`）
9. 负门槛属于券配置错误，必须拒绝（`test_negative_threshold_coupon_is_rejected`）

---

## 2. 有效规格（初始数据）

规格位于 `specs/`，由 `sddctx ingest` 导入 seekdb。

### 2.1 `specs/coupon-rules.md`（SPEC-COUPON v1.3）

| 编号 | 标题 | 状态 | 本次是否为目标 |
| --- | --- | --- | --- |
| REQ-CPN-001 | 满减券按固定金额减免 | 生效中 | 约束（必须保持） |
| REQ-CPN-002 | 折扣券按比例减免 | 生效中 | 约束（必须保持） |
| **REQ-CPN-003** | **门槛判定使用大于等于** | 生效中 | **本次目标** |
| **REQ-CPN-004** | **无门槛券** | 生效中 | **本次目标** |
| REQ-CPN-005 | 优惠金额上限 | 生效中 | 约束（必须保持） |
| REQ-CPN-006 | 运费不参与优惠计算 | 生效中 | 约束（必须保持） |
| REQ-CPN-007 | 金额精度 | 生效中 | 约束（必须保持） |
| **REQ-CPN-008** | **负门槛属于券配置错误** | 生效中 | **本次目标** |

> v1.3 相对 v1.2 修订了 **REQ-CPN-004**、**REQ-CPN-006** 的正文（澄清无门槛券同样受
> 减免上限与运费隔离约束）。这两次修订是**规格演进演示**的一部分，
> 重新导入后的检索结果必须返回新正文，见 §5.4。

### 2.2 `specs/order-settlement-api.md`（SPEC-API v1.1）

REQ-API-001 ~ REQ-API-005，全部「生效中」，属于接口契约约束（必须保持）。

### 2.3 `specs/decisions.md`（SPEC-ADR v1.4）

| 编号 | 状态 | 说明 |
| --- | --- | --- |
| ADR-001 金额一律使用 Decimal | 生效中 | 约束 |
| ADR-002 运费不参与优惠计算 | 生效中 | 约束 |
| **ADR-003 折扣券门槛使用严格大于比较** | **已失效** | 被 REQ-CPN-003 取代，**不得**作为约束 |
| **ADR-004 折扣券必须配置正门槛** | **已失效** | 被 REQ-CPN-004 取代，**不得**作为约束 |
| ADR-005 优惠金额不得超过商品金额 | 生效中 | 约束 |
| ADR-006 无门槛券同样受减免上限与运费隔离约束 | 生效中 | 约束（v1.4 新增） |

> 这两条「已失效」条目是本作品的关键设计点：模块必须在上下文里**单独标出**它们，
> 并注明不得作为约束——否则 AI 助手会按历史决策把门槛改回严格大于。

### 2.4 初始数据（业务工程）

* `demo_app/pricing.py` —— `apply_coupon` / `settle` / `Coupon` / `CouponKind` / `CouponNotApplicable`
* `demo_app/models.py` —— `Order` / `LineItem`
* `demo_app/tests/test_coupon_threshold.py` —— **目标功能测试**（5 条）
* `demo_app/tests/test_coupon_regression.py` —— **回归测试**（9 条）

---

## 3. 上下文中必须包含的来源（核对清单）

`sddctx query "<任务>"` 的输出**必须**能定位到下列来源，且来源真实存在：

| 类别 | 必须出现的来源 |
| --- | --- |
| 适用规格 | `REQ-CPN-003`、`REQ-CPN-004`、`REQ-CPN-008`（以及 REQ-CPN-001/002/005/006/007、REQ-API-*、ADR-001/002/005/006） |
| 已失效条目 | `ADR-003`、`ADR-004`（单独分区，标注「不得作为约束」） |
| 待改代码 | `demo_app/pricing.py` 中的 `apply_coupon`（含 `file:line` 定位） |
| 相关代码 | `Coupon`、`CouponKind`、`CouponNotApplicable`、`settle`、`Order.subtotal` |
| 目标功能测试 | `test_coupon_threshold.py` 的 **5 条**用例 |
| 回归测试 | `test_coupon_regression.py` 的 **9 条**用例 |

> 每条来源都带 `source_ref`（形如 `specs/coupon-rules.md:32#REQ-CPN-003`），
> 可直接回到仓库文件核对。

---

## 4. 操作步骤

### 4.1 前置

```bash
cd minimax
.venv/bin/pip install -r requirements.txt
.venv/bin/powercontext server run --env-file powercontext.env     # 另开一个终端
```

### 4.2 完整流程（一条命令）

```bash
.venv/bin/python scripts/verify.py
```

等价的手工步骤：

```bash
# ① 初始化数据
.venv/bin/python -m sddctx init
.venv/bin/python -m sddctx ingest

# ② 形成开发上下文
.venv/bin/python -m sddctx query "支持无门槛优惠券 threshold=0，门槛判定使用 >=，负门槛视为配置错误"

# ③ 从空状态保存任务记录
.venv/bin/python -m sddctx state save \
  --task-id task-coupon-threshold \
  --task-text "支持无门槛优惠券 threshold=0，门槛判定使用 >=" \
  --status in_progress \
  --done "已导入规格/代码/测试到 seekdb" \
  --done "已按任务组装上下文" \
  --todo "由 MiniMax 完成代码修改" \
  --todo "运行目标功能测试" \
  --todo "运行回归测试"

# ④ **重启上下文模块**（新进程）后读取并复用状态
.venv/bin/python -m sddctx state show
.venv/bin/python -m sddctx query --with-state "支持无门槛优惠券 threshold=0，门槛判定使用 >="

# ⑤ 把上下文交给使用 MiniMax 的开发助手，完成代码修改
.venv/bin/python -m sddctx agent --apply --task-id task-coupon-threshold "<任务描述>"

# ⑥ 功能与回归测试
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_threshold.py  --label target
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_regression.py --label regression
```

---

## 5. 预期结果

### 5.1 修改前（目标行为未实现）

```bash
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_threshold.py --label before-target
```

* **预期**：`5 failed`，`exit_code=1`
* **失败原因**：抛出 `CouponNotApplicable: coupon CPN-NO-THRESHOLD requires a positive threshold`
  —— 属于**目标行为未实现**导致的断言失败，不是环境错误或依赖缺失。

```bash
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_regression.py --label before-regression
```

* **预期**：`9 passed`，`exit_code=0`

### 5.2 修改后

```bash
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_threshold.py --label after-target
```

* **预期**：`5 passed`，`exit_code=0`

```bash
.venv/bin/python -m sddctx test demo_app/tests/test_coupon_regression.py --label after-regression
```

* **预期**：`9 passed`，`exit_code=0`

> **测试集在修改前后保持一致**：测试文件与断言从未被修改，只是实现变了。
> 修改前后对比可直接用 `evidence/before/` 中的快照复核。

### 5.3 上下文正确性

`sddctx query` 的输出必须包含第 3 节的全部来源；并且：

* `ADR-003` / `ADR-004` 必须出现在**「已失效条目」**分区，且带有「不得作为约束」的标注；
* `apply_coupon` 必须带 `specs/...:行号` 与 `demo_app/pricing.py:行号` 形式的定位。

### 5.4 规格变更后重新导入（不得返回固定答案）

本作品已**实跑**过一次完整的规格演进（真的改文件 + 重新导入 + 重新检索），
留证在 `evidence/spec-evolution/`：

| 文件 | 内容 |
| --- | --- |
| `evidence/spec-evolution/before-snapshot.json` | 演进**前**的检索结果快照（`sddctx query --json`） |
| `evidence/spec-evolution/before-query.json` | 演进前的完整上下文 JSON |
| `evidence/spec-evolution/after-ingest-specs.json` | 应用规格修订那次导入的变更清单 |
| `evidence/spec-evolution/after-snapshot.json` | 演进**后**的检索结果快照 |
| `evidence/spec-evolution/after-query.json` | 演进后的完整上下文 JSON |
| `evidence/spec-evolution/after-ingest-code-rollback.json` | 代码回退到「修改前」状态那次导入的变更清单 |

演进动作（真实发生）：

1. `specs/coupon-rules.md` v1.2 → **v1.3**：修订 `REQ-CPN-004`、`REQ-CPN-006` 的正文；
2. `specs/decisions.md` v1.3 → **v1.4**：新增 `ADR-006`；
3. `sddctx ingest` 重新导入；
4. `sddctx query --json` 重新检索。

结果（对比 before / after 快照）：

| 条目 | 演进前 | 演进后 | 说明 |
| --- | --- | --- | --- |
| REQ-CPN-004 正文 | v1.2 旧措辞 | **v1.3 新措辞**（含「v1.3 修订：…」） | `content_sha256`：`272be2df0488…` → `63905905d438…` |
| REQ-CPN-006 正文 | v1.2 旧措辞 | **v1.3 新措辞**（含「v1.3 修订：…」） | `content_sha256`：`d92d84ae7ccd…` → `b172b6d4429d…` |
| ADR-006 | **不存在** | **存在**（新增生效决策，出现在上下文「适用规格」分区） | 新增 `content_sha256 = ba1dabf6a947…` |
| REQ-CPN-003 / REQ-CPN-008 | 生效中 | 生效中（正文未变，sha 不变） | 未受影响的条目保持稳定 |

导入端的变更清单（`evidence/spec-evolution/after-ingest-specs.json`）：

```json
"changed": [
  {"doc_id": "REQ-CPN-004", "action": "updated", "doc_type": "spec"},
  {"doc_id": "REQ-CPN-006", "action": "updated", "doc_type": "spec"},
  {"doc_id": "ADR-006", "action": "inserted", "doc_type": "spec"}
]
```

> 结论：模块**没有**返回固定答案——修改规格并重新导入后，检索结果返回的是**更新后的正文**，
> 新出现的条目也会立刻进入上下文。
> 同一套实现可以在 OceanBase 集群上跑（`SDDCTX_DB_MODE=server`），SQL 完全一致。

可复现步骤：

```bash
# ① 修改 specs/ 下的任意条目正文（或新增条目）
# ② 重新导入
.venv/bin/python -m sddctx ingest
# ③ 再次检索，输出必须反映更新后的内容
.venv/bin/python -m sddctx query --json "<任务>" | \
  python -c "import json,sys; b=json.load(sys.stdin); print([s['body'] for s in b['specs'] if s['spec_id']=='REQ-CPN-004'])"
# ④ 对照数据库里的内容指纹
.venv/bin/python -m sddctx db --sql \
  "SELECT spec_id, status, content_sha256 FROM spec_item WHERE spec_id IN ('REQ-CPN-004','REQ-CPN-006','ADR-006')"
```

### 5.5 数据库确实参与

```bash
.venv/bin/python -m sddctx db --stats
```

* **预期**：`spec_item / code_symbol / test_case / doc_index / doc_token` 行数均 > 0。

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT spec_id, status FROM spec_item ORDER BY status, spec_id"
```

* **预期**：能查到 `REQ-CPN-003`、`ADR-003` 等条目，且 `ADR-003`/`ADR-004` 的
  `status` 为 `superseded`。

```bash
.venv/bin/python -m sddctx db --sql \
  "SELECT d.ref_id, MATCH(d.search_text) AGAINST ('门槛' IN BOOLEAN MODE) AS relevance \
   FROM doc_index d WHERE MATCH(d.search_text) AGAINST ('门槛' IN BOOLEAN MODE) \
   ORDER BY relevance DESC LIMIT 5"
```

* **预期**：命中 `REQ-CPN-003` / `REQ-CPN-004` 等条目——证明检索确实发生在数据库里，
  而不是把资料读进内存后自己排序。

### 5.6 状态能够复用

```bash
.venv/bin/python -m sddctx state show
```

* **预期**：返回 `current`（含 `done_items` / `todo_items` / `validation_results`）
  与 `history_count` ≥ 1。
* **关键点**：该命令是**独立进程**（`python -m sddctx state show`），
  与写入状态的进程不同——证明状态确实落在 PowerContext 持久化存储里，
  模块重启后仍可读取。

```bash
.venv/bin/python -m sddctx query --with-state "<任务>"
```

* **预期**：上下文 Markdown 里出现「任务进展（来自 PowerContext 恢复）」小节，
  内容与 `state show` 一致——证明恢复出的状态被真正用于后续上下文构造。

---

## 6. 重置方法

```bash
cd minimax
# 停止 PowerContext（Ctrl-C）后：
rm -rf .sddctx            # 嵌入式 seekdb 数据目录 + PowerContext 的 SQLite 库
rm -rf evidence           # 可选：清空留证
```

也可以只重置其中一部分：

| 想重置什么 | 操作 |
| --- | --- |
| 数据库 | `rm -rf .sddctx/seekdb`，再 `sddctx init && sddctx ingest` |
| 任务状态 | `rm -rf .sddctx/powercontext`，重启 PowerContext Server |
| 代码改动 | `cp evidence/before/demo_app/pricing.py demo_app/pricing.py` |
| 全部 | `rm -rf .sddctx evidence` |

重置后 `sddctx state show` 应返回 `current: null`、`history_count: 0`，
即回到**空状态**，随后重新执行第 4 节步骤即可完整复现。

---

## 7. 留证清单

| 文件 | 内容 |
| --- | --- |
| `evidence/verify-latest.json` | 端到端验证报告（每一步的 ok 标志与输出，本轮 10 步全部 ok） |
| `evidence/20261114-100427-agent-task-coupon-threshold.json` | Agent 运行报告（上下文摘要、模型改动、测试结果、状态写入凭证） |
| `evidence/minimax/20261114-100423-task-coupon-threshold.json` | **MiniMax 请求与响应原文**（system prompt、user prompt、模型输出、token 用量） |
| `evidence/20261114-100319-pytest-before-target.json` | 修改前目标功能测试日志（5 failed，exit_code=1） |
| `evidence/20261114-100321-pytest-before-regression.json` | 修改前回归测试日志（9 passed，exit_code=0） |
| `evidence/20261114-100429-pytest-after-target.json` | 修改后目标功能测试日志（5 passed，exit_code=0） |
| `evidence/20261114-100431-pytest-after-regression.json` | 修改后回归测试日志（9 passed，exit_code=0） |
| `evidence/20261114-100344-ingest.json` | 本轮导入的变更清单（inserted / updated / removed / unchanged） |
| `evidence/before/` | 修改前的源码与测试快照（`demo_app/pricing.py`、`demo_app/models.py`、两个测试文件） |
| `evidence/change.patch` | 最终代码差异（+2 / −2） |
| `evidence/context-latest.md` | 最近一次生成的完整上下文（交给 MiniMax 的那份） |
| `evidence/spec-evolution/` | 规格演进前后的检索快照与导入变更清单（见 §5.4） |
| `docs/DB-QUERY-LOG.md` | 数据库查询记录 |
| `docs/POWCONTEXT-STATE-LOG.md` | PowerContext 状态写入与复用记录 |
| `docs/MINIMAX-DEVLOG.md` | MiniMax 开发记录 |
