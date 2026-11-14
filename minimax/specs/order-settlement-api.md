# SPEC-API 结算接口契约

- 规格编号：SPEC-API
- 版本：1.1
- 状态：生效中
- 适用模块：demo_app.pricing, demo_app.models
- 关键符号：settle, SettlementResult, apply_coupon

本规格描述优惠结算对外接口的可观察契约。需求编号为 `REQ-API-NNN`。

## REQ-API-001 结算结果字段

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：settle, SettlementResult
- 标签：api, settlement, 契约

`settle(order, coupon=None)` 返回 `SettlementResult`，至少包含 `subtotal`、`discount`、
`freight`、`payable` 四个字段，字段类型均为 `Decimal`。

## REQ-API-002 应付金额包含运费

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：settle
- 标签：api, settlement, 运费

应付金额 = 商品小计 − 优惠金额 + 运费。运费在优惠计算完成后计入应付金额。

## REQ-API-003 无优惠券时的结算

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：settle
- 标签：api, settlement, 契约

不传优惠券时，优惠金额为 0，结算正常返回，不抛出异常。

## REQ-API-004 券不适用时抛出 CouponNotApplicable

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：apply_coupon, CouponNotApplicable
- 标签：api, error, 契约

订单不满足券的使用条件时，`apply_coupon` 必须抛出 `CouponNotApplicable`，异常信息包含券编号。

## REQ-API-005 结算入口不接受原始字典

- 状态：生效中
- 适用模块：demo_app.pricing
- 关键符号：settle, apply_coupon
- 标签：api, typing, 契约

`settle` 与 `apply_coupon` 只接受 `Order` / `Coupon` 领域对象，不接受原始字典，
调用方需自行构造领域对象。
