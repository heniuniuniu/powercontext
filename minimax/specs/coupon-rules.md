# SPEC-COUPON 订单优惠结算规格

- 规格编号：SPEC-COUPON
- 版本：1.3
- 状态：生效中
- 适用模块：demo_app.pricing, demo_app.models
- 关键符号：apply_coupon, settle, Coupon, CouponKind, CouponNotApplicable

本规格描述订单优惠结算的可观察行为。每条需求以独立小节给出，编号为 `REQ-CPN-NNN`，
是任务上下文里可被精确定位的最小单位。

## REQ-CPN-001 满减券按固定金额减免

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：coupon, threshold, 满减

满减券（`CouponKind.THRESHOLD`）在订单满足使用门槛时，按券的 `value` 减免固定金额，
不受订单金额比例影响。

## REQ-CPN-002 折扣券按比例减免

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：coupon, discount, 折扣

折扣券（`CouponKind.DISCOUNT`）在订单满足使用门槛时，按券的 `value` 比例对**商品小计**
减免，比例为小数形式（例如 0.8 表示八折）。

## REQ-CPN-003 门槛判定使用大于等于

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：coupon, threshold, 边界, 门槛

订单**商品小计大于或等于**券门槛时，券即可使用；恰好等于门槛的订单同样可以使用。
门槛判定只针对 `threshold > 0` 的券。

## REQ-CPN-004 无门槛券

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon, Coupon
- 标签：coupon, threshold, 边界, 无门槛

券的 `threshold` 为 0 表示**无门槛券**：不做任何门槛判定，任意非空订单都可以使用；
券的减免额仍然受 `max_discount` 封顶，且不得超过订单商品小计，运费照常隔离在优惠基数之外。
（v1.3 修订：明确无门槛券同样受减免上限与运费隔离约束，门槛判定被跳过不代表其他规则失效。）

## REQ-CPN-005 优惠金额上限

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：coupon, cap, 上限

单笔优惠金额不得超过券的 `max_discount`，且优惠金额不得超过订单商品小计。

## REQ-CPN-006 运费不参与优惠计算

- 状态：生效中
- 适用模块：demo_app.pricing, demo_app.models
- 关键符号：apply_coupon, Order.subtotal
- 标签：coupon, freight, 运费

门槛判定与优惠金额计算都只使用订单**商品小计**，运费不计入优惠基数；
无门槛券（`threshold = 0`）同样不把运费计入优惠基数。
（v1.3 修订：补充无门槛券在运费隔离上的口径，与 ADR-002、ADR-006 保持一致。）

## REQ-CPN-007 金额精度

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon, settle
- 标签：money, decimal, 精度

所有金额使用 `Decimal` 计算，对外返回的金额统一保留两位小数，采用四舍五入
（`ROUND_HALF_UP`）。

## REQ-CPN-008 负门槛属于券配置错误

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon
- 标签：coupon, threshold, 边界, 配置校验

券的 `threshold` 为负数属于**券配置错误**，`apply_coupon` 必须抛出
`CouponNotApplicable`（与引入无门槛券之前的既有行为保持一致）。
只有 `threshold == 0`（无门槛）与 `threshold > 0`（有门槛）两种取值是合法的。
