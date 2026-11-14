# MiniMax 开发记录

> 记录本次任务中 **MiniMax 实际参与的代码生成与修改**全过程。
> 原始请求与响应见 `evidence/minimax/20261114-100423-task-coupon-threshold.json`。

---

## 1. 任务

| 项 | 内容 |
| --- | --- |
| 任务 ID | `task-coupon-threshold` |
| 任务描述 | 支持无门槛优惠券（`coupon.threshold = 0`）：无门槛券不做门槛判定即可使用；订单商品金额恰好等于券门槛时，折扣券也应当可以使用（门槛判定使用 `>=` 而不是 `>`）。请只修改 `demo_app/pricing.py` 中 `apply_coupon` 的门槛判定逻辑，不要改变满减券/折扣券的减免计算、上限封顶、运费隔离与金额精度等既有行为。 |
| 被修改工程 | `demo_app/`（订单优惠结算服务，Python，pytest） |
| 改动目标 | `demo_app/pricing.py` → `apply_coupon(order, coupon)` |

---

## 2. 提供给 MiniMax 的上下文

上下文由 `sddctx` 从 **seekdb** 检索并组装，完整文本见 `evidence/context-latest.md`，
原始请求体（system + user prompt）见 `evidence/minimax/20261114-100423-task-coupon-threshold.json`
的 `system_prompt` / `user_prompt` 字段。

### 2.1 上下文里有什么

| 分区 | 内容 | 数量 |
| --- | --- | --- |
| 适用规格条目（**必须遵守**） | REQ-CPN-001 ~ REQ-CPN-008、REQ-API-002/003/004、ADR-001/002/005/006 | 12 |
| 已失效条目（**不得**作为约束） | ADR-003、ADR-004 | 2 |
| 需要修改的代码 | `demo_app.pricing.apply_coupon` 等 9 个符号（含 `file:line`） | 9 |
| 目标功能测试 | `test_coupon_threshold.py` 的 5 条 | 5 |
| 回归测试 | `test_coupon_regression.py` 的 9 条 | 9 |
| 任务进展 | 来自 PowerContext 恢复的已完成/待办 | 1 |

每条都带 `source_ref`（例如 `specs/coupon-rules.md:32#REQ-CPN-003`），可回溯到仓库原文。

> `ADR-006` 是**规格演进后新增**的条目：规格从 v1.3 修订到 v1.4 并重新导入后，
> 它才第一次出现在上下文里（详见 `docs/VERIFICATION.md` §5.4）。

### 2.2 检索确实发生在数据库里

上下文里的每一条都来自 seekdb 的实际查询结果，而不是把资料读进内存后自己排序。
原生全文检索（`FULLTEXT INDEX … WITH PARSER ik`）对「门槛」的命中与相关度：

| 文档 | relevance |
| --- | --- |
| `REQ-CPN-003` | 0.4442 |
| `ADR-006`（v1.4 新增） | 0.3876 |
| `ADR-003`（已失效） | 0.3677 |
| `REQ-CPN-004` | 0.3660 |
| `ADR-004`（已失效） | 0.3577 |
| `REQ-CPN-008` | 0.3522 |

完整 SQL 与结果见 `docs/DB-QUERY-LOG.md`。

---

## 3. MiniMax 的调用

| 项 | 值 |
| --- | --- |
| 接入方式 | **HTTP API**，Anthropic Messages 协议 `POST {base_url}/messages` |
| 端点 | `https://agent.minimax.cn/mavis/api/v1/llm/v1/messages` |
| 模型 | `MiniMax-M3.1-Flash` |
| HTTP 状态 | 200 |
| `stop_reason` | `end_turn` |
| 尝试次数 | 1（首次即产出补丁） |
| token 用量 | input 1 + cache_read 3665 / output 887 / thinking 643 |

### 3.1 模型输出（原文）

```
<<<PATCH file=demo_app/pricing.py function=apply_coupon>>>
def apply_coupon(order: Order, coupon: Coupon) -> Decimal:
    """计算优惠券对订单产生的优惠金额。
    ...
    """
    subtotal = order.subtotal
    if coupon.threshold < 0:
        raise CouponNotApplicable(
            f"coupon {coupon.coupon_id} requires a positive threshold"
        )
    if coupon.threshold > 0 and subtotal < coupon.threshold:
        raise CouponNotApplicable(
            f"coupon {coupon.coupon_id} requires subtotal >= {coupon.threshold}"
        )
    if coupon.kind is CouponKind.THRESHOLD:
        discount = coupon.value
    else:
        discount = subtotal * coupon.value
    discount = min(discount, coupon.max_discount)
    discount = min(discount, subtotal)
    return _round_money(discount)
<<<END PATCH>>>
```

模型逐条遵守了上下文里的规格：

* REQ-CPN-003（门槛用 `>=`）→ `coupon.threshold > 0 and subtotal < coupon.threshold`
* REQ-CPN-004（`threshold == 0` 为无门槛券）→ 门槛判断前先加 `coupon.threshold > 0`
* REQ-CPN-008（负门槛是配置错误）→ 保留 `coupon.threshold < 0` 的拒绝逻辑
* REQ-CPN-001/002/005/006/007（减免计算、上限、运费隔离、精度）→ 逐字未动

---

## 4. 落盘与验证

模块用 `ast` 定位 `apply_coupon` 的行区间（原文第 61–89 行），把补丁原地替换进去，
文件其余部分**逐字节不变**；替换后做 `ast.parse` 语法校验，通过才写盘。

| 项 | 值 |
| --- | --- |
| 补丁模式 | `function-splice` |
| 代码差异 | **+2 行 / −2 行**（见 `evidence/change.patch`） |
| 改动行以外的原文 | **2859 字节逐字节未变**（模块自报的 `unchanged_bytes`） |
| 行尾风格 | 与原文件一致（LF），整文件未被改写 |
| 目标功能测试（修改后） | `5 passed`，`exit_code=0` |
| 回归测试（修改后） | `9 passed`，`exit_code=0` |
| 目标功能测试（修改前） | `5 failed`，`exit_code=1`（`CouponNotApplicable`，目标行为未实现） |
| 回归测试（修改前） | `9 passed`，`exit_code=0` |

最终差异（`evidence/change.patch`）：

```diff
--- a/demo_app/pricing.py
+++ b/demo_app/pricing.py
@@ -72,11 +72,11 @@
         CouponNotApplicable: 订单不满足该券的使用条件。
     """
     subtotal = order.subtotal
-    if coupon.threshold <= 0:
+    if coupon.threshold < 0:
         raise CouponNotApplicable(
             f"coupon {coupon.coupon_id} requires a positive threshold"
         )
-    if subtotal <= coupon.threshold:
+    if coupon.threshold > 0 and subtotal < coupon.threshold:
         raise CouponNotApplicable(
             f"coupon {coupon.coupon_id} requires subtotal >= {coupon.threshold}"
         )
```

**与提交代码差异的对应关系**：`evidence/change.patch` 就是仓库中 `demo_app/pricing.py`
相对 `evidence/before/demo_app/pricing.py` 的全部差异，两者逐字节一致。
测试文件在修改前后完全相同（快照见 `evidence/before/demo_app/tests/`）。

---

## 5. 开发过程中的三次返工（如实记录）

这套接入最初不是一次成功的，如实记录以便复核：

### 5.1 第一次：整文件替换模式 —— 逻辑正确，但带无关改动

让模型输出整个文件时，它**正确实现了目标逻辑**，但同时顺手改了：

* 模块 docstring（删掉了规格出处说明）
* `from decimal import ROUND_HALF_UP, Decimal` → `from decimal import Decimal, ROUND_HALF_UP`
* `SettlementResult` 的 docstring
* 文件末尾换行符

> 这些改动不破坏测试，但污染了差异，与题目「局部修改」的要求不符。

**改进**：把「整文件替换」换成**函数级补丁**（模型只返回要改的函数，模块用 `ast` 原地替换）。
无关改动从此**结构上不可能发生**，不再依赖提示词约束。

### 5.2 第二次：输出预算被思维链吃光 —— 模型根本没产出补丁

加严提示词后的那次调用返回：

```
stop_reason: "max_tokens"
usage.output_tokens: 8192
usage.output_tokens_details.thinking_tokens: 8191
content: [{"type": "thinking", ...}]      # 没有任何 text 块
```

`MiniMax-M3.1-Flash` 强制自适应思维链，思维链把 8192 的输出预算全部用尽，
模型**一个补丁字符都没输出**。

**改进**（两项都保留在最终实现里）：

1. 请求体带 `thinking: {type: "enabled", budget_tokens: 0}`，把思维链压到极短
   （最终一次调用 thinking 仅 643 token）；
2. `stop_reason == "max_tokens"` 且正文为空时，自动把 `max_tokens` 翻倍重试
   （最多 3 次），全部尝试记录在证据的 `attempts` 字段里。

> 顺带验证过：`thinking: {type: "disabled"}` 会被该模型拒绝（`requires adaptive thinking`），
> `budget_tokens: 0` 是可用写法。

### 5.3 第三次：Windows 行尾把整个文件改写了 —— 「其余字节不变」曾经不成立

函数级替换改完后，用**逐字节比对**核对前后文件时发现：内容只变了 2 行，
但**文件每一行**都被改了——因为落盘时 `write_text` 默认把 `\n` 转成了 `\r\n`（Windows），
而原文件是 LF。结果是 `git diff` 只看内容没问题，但字节级差异是整个文件。

**改进**：

* 读、写都改用 `newline=""`，**原样保留**原文件行尾；
* 新增 `unchanged_bytes` 指标，用 `difflib.SequenceMatcher` 统计**逐字节未改动**的部分；
* 写回前把归一化的 LF 内容还原成原文件行尾。

修复后：`unchanged_bytes = 2859`，逐字节比对确认**只有 2 行不同**，
文件整体仍是 LF。教训是：**「只改了目标函数」这种承诺必须用字节级证据自证**，
不能只看 `git diff` 的结论。

---

## 6. 证据文件索引

| 文件 | 内容 |
| --- | --- |
| `evidence/minimax/20261114-100423-task-coupon-threshold.json` | **MiniMax 请求与响应原文**：endpoint、model、system_prompt、user_prompt、response_body、usage、attempts |
| `evidence/context-latest.md` | 交给 MiniMax 的完整上下文 |
| `evidence/change.patch` | 最终代码差异（+2 / −2） |
| `evidence/20261114-100427-agent-task-coupon-threshold.json` | Agent 运行报告：上下文摘要、模型改动、测试结果、状态写入凭证 |
| `evidence/20261114-100319-pytest-before-target.json` | 修改前目标功能测试日志（命令、退出码、完整输出） |
| `evidence/20261114-100321-pytest-before-regression.json` | 修改前回归测试日志 |
| `evidence/20261114-100429-pytest-after-target.json` | 修改后目标功能测试日志（5 passed） |
| `evidence/20261114-100431-pytest-after-regression.json` | 修改后回归测试日志（9 passed） |
| `evidence/20261114-100344-ingest.json` | 本轮导入的变更清单 |
| `evidence/verify-latest.json` | 端到端验证报告（10 个步骤全部 ok） |
