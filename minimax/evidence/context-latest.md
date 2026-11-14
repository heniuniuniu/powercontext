# SDD 任务上下文

## 任务

支持无门槛优惠券（coupon.threshold = 0）：无门槛券不做门槛判定即可使用；并且订单商品金额恰好等于券门槛时，折扣券也应当可以使用（门槛判定使用 >= 而不是 >）。请只修改 demo_app/pricing.py 中 apply_coupon 的门槛判定逻辑，不要改变满减券/折扣券的减免计算、上限封顶、运费隔离与金额精度等既有行为。

## 适用规格条目（当前有效，必须遵守）

### REQ-CPN-004 无门槛券
- 来源：specs/coupon-rules.md:42#REQ-CPN-004
- 状态：生效中
- 涉及符号：apply_coupon, Coupon

券的 `threshold` 为 0 表示**无门槛券**：不做任何门槛判定，任意非空订单都可以使用；
券的减免额仍然受 `max_discount` 封顶，且不得超过订单商品小计，运费照常隔离在优惠基数之外。
（v1.3 修订：明确无门槛券同样受减免上限与运费隔离约束，门槛判定被跳过不代表其他规则失效。）

### REQ-CPN-008 负门槛属于券配置错误
- 来源：specs/coupon-rules.md:83#REQ-CPN-008
- 状态：生效中
- 涉及符号：apply_coupon

券的 `threshold` 为负数属于**券配置错误**，`apply_coupon` 必须抛出
`CouponNotApplicable`（与引入无门槛券之前的既有行为保持一致）。
只有 `threshold == 0`（无门槛）与 `threshold > 0`（有门槛）两种取值是合法的。

### REQ-CPN-003 门槛判定使用大于等于
- 来源：specs/coupon-rules.md:32#REQ-CPN-003
- 状态：生效中
- 涉及符号：apply_coupon

订单**商品小计大于或等于**券门槛时，券即可使用；恰好等于门槛的订单同样可以使用。
门槛判定只针对 `threshold > 0` 的券。

### REQ-CPN-006 运费不参与优惠计算
- 来源：specs/coupon-rules.md:62#REQ-CPN-006
- 状态：生效中
- 涉及符号：apply_coupon, Order.subtotal

门槛判定与优惠金额计算都只使用订单**商品小计**，运费不计入优惠基数；
无门槛券（`threshold = 0`）同样不把运费计入优惠基数。
（v1.3 修订：补充无门槛券在运费隔离上的口径，与 ADR-002、ADR-006 保持一致。）

### ADR-006 无门槛券同样受减免上限与运费隔离约束
- 来源：specs/decisions.md:62#ADR-006
- 状态：生效中
- 涉及符号：apply_coupon

引入无门槛券（`threshold == 0`）之后，**门槛判定**被跳过，但**减免上限**（`max_discount`）、
**不超过商品金额**（ADR-005）与**运费隔离**（ADR-002）对无门槛券同样成立。
该决策澄清了 ADR-005 的适用范围：封顶规则不区分券是否设置了门槛。

### REQ-CPN-001 满减券按固定金额减免
- 来源：specs/coupon-rules.md:12#REQ-CPN-001
- 状态：生效中
- 涉及符号：apply_coupon

满减券（`CouponKind.THRESHOLD`）在订单满足使用门槛时，按券的 `value` 减免固定金额，
不受订单金额比例影响。

### REQ-CPN-005 优惠金额上限
- 来源：specs/coupon-rules.md:53#REQ-CPN-005
- 状态：生效中
- 涉及符号：apply_coupon

单笔优惠金额不得超过券的 `max_discount`，且优惠金额不得超过订单商品小计。

### REQ-CPN-002 折扣券按比例减免
- 来源：specs/coupon-rules.md:22#REQ-CPN-002
- 状态：生效中
- 涉及符号：apply_coupon

折扣券（`CouponKind.DISCOUNT`）在订单满足使用门槛时，按券的 `value` 比例对**商品小计**
减免，比例为小数形式（例如 0.8 表示八折）。

### ADR-005 优惠金额不得超过商品金额
- 来源：specs/decisions.md:53#ADR-005
- 状态：生效中
- 涉及符号：apply_coupon

即使券的减免额大于订单商品金额，也只减到商品金额为止。

### ADR-002 运费不参与优惠计算
- 来源：specs/decisions.md:20#ADR-002
- 状态：生效中
- 涉及符号：apply_coupon, Order.subtotal

优惠基数与门槛判定都使用商品小计，运费只在应付金额阶段加入。

### REQ-API-004 券不适用时抛出 CouponNotApplicable
- 来源：specs/order-settlement-api.md:39#REQ-API-004
- 状态：生效中
- 涉及符号：apply_coupon, CouponNotApplicable

订单不满足券的使用条件时，`apply_coupon` 必须抛出 `CouponNotApplicable`，异常信息包含券编号。

### REQ-API-003 无优惠券时的结算
- 来源：specs/order-settlement-api.md:30#REQ-API-003
- 状态：生效中
- 涉及符号：settle

不传优惠券时，优惠金额为 0，结算正常返回，不抛出异常。

## 已失效条目（仅供追溯，**不得**作为约束）

- ADR-004 折扣券必须配置正门槛 —— 来源：specs/decisions.md:41#ADR-004；失效原因：REQ-CPN-004 引入无门槛券
- ADR-003 折扣券门槛使用严格大于比较 —— 来源：specs/decisions.md:29#ADR-003；失效原因：REQ-CPN-003 明确门槛判定使用大于等于

## 需要修改的代码

### demo_app.pricing.apply_coupon  (function)
- 来源：demo_app/pricing.py:61-89#apply_coupon
- 签名：def apply_coupon(order: Order, coupon: Coupon) -> Decimal: """计算优惠券对订单产生的优惠金额。

```python
def apply_coupon(order: Order, coupon: Coupon) -> Decimal:
    """计算优惠券对订单产生的优惠金额。

    Args:
        order: 待结算订单。
        coupon: 待使用的优惠券。

    Returns:
        优惠金额（两位小数）。

    Raises:
        CouponNotApplicable: 订单不满足该券的使用条件。
    """
    subtotal = order.subtotal
    if coupon.threshold <= 0:
        raise CouponNotApplicable(
            f"coupon {coupon.coupon_id} requires a positive threshold"
        )
    if subtotal <= coupon.threshold:
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
```

### demo_app.pricing.Coupon  (class)
- 来源：demo_app/pricing.py:28-43#Coupon
- 签名：class Coupon: """优惠券。

```python
class Coupon:
    """优惠券。

    Attributes:
        coupon_id: 券编号。
        kind: 券类型。
        threshold: 使用门槛，0 表示无门槛券。
        value: 满减券为减免金额，折扣券为折扣比例（0 < value < 1）。
        max_discount: 单笔最大减免金额。
    """

    coupon_id: str
    kind: CouponKind
    threshold: Decimal
    value: Decimal
    max_discount: Decimal
```

### demo_app.pricing.settle  (function)
- 来源：demo_app/pricing.py:92-106#settle
- 签名：def settle(order: Order, coupon: Coupon | None = None) -> SettlementResult: """结算订单，返回商品小计、优惠金额、运费与应付金额。

```python
def settle(order: Order, coupon: Coupon | None = None) -> SettlementResult:
    """结算订单，返回商品小计、优惠金额、运费与应付金额。

    运费不参与优惠计算，但计入应付金额。
    """
    subtotal = order.subtotal
    freight = order.freight
    discount = Decimal("0") if coupon is None else apply_coupon(order, coupon)
    payable = subtotal - discount + freight
    return SettlementResult(
        subtotal=subtotal,
        discount=discount,
        freight=freight,
        payable=_round_money(payable),
    )
```

### demo_app.pricing.CouponKind  (class)
- 来源：demo_app/pricing.py:20-24#CouponKind
- 签名：class CouponKind(str, Enum): """券类型。"""

```python
class CouponKind(str, Enum):
    """券类型。"""

    THRESHOLD = "threshold"  # 满减券：满 threshold 减 value
    DISCOUNT = "discount"  # 折扣券：按 value 比例减免
```

### demo_app.models.Order  (class)
- 来源：demo_app/models.py:28-41#Order
- 签名：class Order: """订单。``freight`` 为运费，不参与优惠计算。"""

```python
class Order:
    """订单。``freight`` 为运费，不参与优惠计算。"""

    order_id: str
    items: tuple[LineItem, ...]
    freight: Decimal = Decimal("0")

    @property
    def subtotal(self) -> Decimal:
        """商品小计（不含运费）。"""
        total = Decimal("0")
        for item in self.items:
            total += item.line_total
        return total
```

### demo_app.pricing.CouponNotApplicable  (class)
- 来源：demo_app/pricing.py:16-17#CouponNotApplicable
- 签名：class CouponNotApplicable(Exception): """优惠券不适用于该订单。"""

```python
class CouponNotApplicable(Exception):
    """优惠券不适用于该订单。"""
```

### demo_app.models.Order.subtotal  (method)
- 来源：demo_app/models.py:36-41#subtotal
- 签名：def subtotal(self) -> Decimal: """商品小计（不含运费）。"""

```python
    def subtotal(self) -> Decimal:
        """商品小计（不含运费）。"""
        total = Decimal("0")
        for item in self.items:
            total += item.line_total
        return total
```

### demo_app.pricing._round_money  (function)
- 来源：demo_app/pricing.py:56-58#_round_money
- 签名：def _round_money(value: Decimal) -> Decimal: """金额统一保留两位小数，四舍五入。"""

```python
def _round_money(value: Decimal) -> Decimal:
    """金额统一保留两位小数，四舍五入。"""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

### demo_app.pricing.SettlementResult  (class)
- 来源：demo_app/pricing.py:47-53#SettlementResult
- 签名：class SettlementResult: """结算结果。"""

```python
class SettlementResult:
    """结算结果。"""

    subtotal: Decimal
    discount: Decimal
    freight: Decimal
    payable: Decimal
```

## 验证测试

### 目标功能测试（修改前失败、修改后必须通过）

- `test_no_threshold_discount_coupon_is_applicable` —— demo_app/tests/test_coupon_threshold.py:37-41::test_no_threshold_discount_coupon_is_applicable
- `test_discount_coupon_applies_at_exact_threshold` —— demo_app/tests/test_coupon_threshold.py:44-48::test_discount_coupon_applies_at_exact_threshold
- `test_no_threshold_coupon_base_excludes_freight` —— demo_app/tests/test_coupon_threshold.py:62-66::test_no_threshold_coupon_base_excludes_freight
- `test_no_threshold_coupon_respects_max_discount` —— demo_app/tests/test_coupon_threshold.py:51-59::test_no_threshold_coupon_respects_max_discount
- `test_no_threshold_coupon_in_settlement` —— demo_app/tests/test_coupon_threshold.py:69-75::test_no_threshold_coupon_in_settlement

### 回归测试（修改前后都必须通过）

- `test_threshold_coupon_reduces_fixed_amount` —— demo_app/tests/test_coupon_regression.py:49-57::test_threshold_coupon_reduces_fixed_amount
- `test_discount_never_exceeds_subtotal` —— demo_app/tests/test_coupon_regression.py:67-75::test_discount_never_exceeds_subtotal
- `test_discount_coupon_rejected_when_below_threshold` —— demo_app/tests/test_coupon_regression.py:42-46::test_discount_coupon_rejected_when_below_threshold
- `test_negative_threshold_coupon_is_rejected` —— demo_app/tests/test_coupon_regression.py:113-117::test_negative_threshold_coupon_is_rejected
- `test_discount_coupon_respects_max_discount` —— demo_app/tests/test_coupon_regression.py:60-64::test_discount_coupon_respects_max_discount
- `test_settlement_without_coupon` —— demo_app/tests/test_coupon_regression.py:97-102::test_settlement_without_coupon
- `test_freight_excluded_from_coupon_base` —— demo_app/tests/test_coupon_regression.py:78-83::test_freight_excluded_from_coupon_base
- `test_not_applicable_error_mentions_coupon_id` —— demo_app/tests/test_coupon_regression.py:105-110::test_not_applicable_error_mentions_coupon_id
- `test_settlement_total_includes_freight` —— demo_app/tests/test_coupon_regression.py:86-94::test_settlement_total_includes_freight

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
