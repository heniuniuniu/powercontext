# 决策记录（ADR）

- 规格编号：SPEC-ADR
- 版本：1.4
- 状态：生效中
- 适用模块：demo_app.pricing, demo_app.models

本文件记录已经做出的设计决策。**已失效**的决策不得再作为任务约束，
但仍需保留，用于解释历史行为与追溯变更原因。编号为 `ADR-NNN`。

## ADR-001 金额一律使用 Decimal

- 状态：生效中
- 适用模块：demo_app.pricing, demo_app.models
- 关键符号：apply_coupon, settle
- 标签：decision, money, decimal

禁止使用 float 表示金额，避免二进制浮点误差累积。

## ADR-002 运费不参与优惠计算

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon, Order.subtotal
- 标签：decision, freight, 运费

优惠基数与门槛判定都使用商品小计，运费只在应付金额阶段加入。

## ADR-003 折扣券门槛使用严格大于比较

- 状态：已失效
- 失效原因：REQ-CPN-003 明确门槛判定使用大于等于
- 失效时间：2026-03-18
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：decision, threshold, 边界

历史决策：门槛判定使用严格大于（`subtotal > threshold`），恰好等于门槛不可用。
该决策已被 REQ-CPN-003 取代，不再约束当前实现。

## ADR-004 折扣券必须配置正门槛

- 状态：已失效
- 失效原因：REQ-CPN-004 引入无门槛券
- 失效时间：2026-03-18
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon, Coupon
- 标签：decision, threshold, 无门槛

历史决策：所有券必须配置正门槛，`threshold <= 0` 视为券配置错误。
该决策已被 REQ-CPN-004 取代，不再约束当前实现。

## ADR-005 优惠金额不得超过商品金额

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：decision, cap, 上限

即使券的减免额大于订单商品金额，也只减到商品金额为止。

## ADR-006 无门槛券同样受减免上限与运费隔离约束

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：decision, threshold, 无门槛, cap, 运费

引入无门槛券（`threshold == 0`）之后，**门槛判定**被跳过，但**减免上限**（`max_discount`）、
**不超过商品金额**（ADR-005）与**运费隔离**（ADR-002）对无门槛券同样成立。
该决策澄清了 ADR-005 的适用范围：封顶规则不区分券是否设置了门槛。
