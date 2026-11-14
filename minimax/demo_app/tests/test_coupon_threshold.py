"""目标功能测试：本次任务要新增/修正的行为。

对应规格：REQ-CPN-003（门槛判定使用大于等于）、REQ-CPN-004（无门槛券）。

修改前：这些用例因目标行为未实现而失败（断言失败，不是环境错误）。
修改后：这些用例全部通过。
"""

from __future__ import annotations

from decimal import Decimal

from demo_app.models import LineItem, Order
from demo_app.pricing import Coupon, CouponKind, apply_coupon, settle


def _order(subtotal: str = "100.00", freight: str = "0.00") -> Order:
    return Order(
        order_id="SO-TARGET",
        items=(LineItem("SKU-1", "商品", Decimal(subtotal), 1),),
        freight=Decimal(freight),
    )


def _coupon(**kwargs) -> Coupon:
    params = {
        "coupon_id": "CPN-NO-THRESHOLD",
        "kind": CouponKind.DISCOUNT,
        "threshold": Decimal("0.00"),
        "value": Decimal("0.9"),
        "max_discount": Decimal("1000.00"),
    }
    params.update(kwargs)
    return Coupon(**params)


def test_no_threshold_discount_coupon_is_applicable() -> None:
    """REQ-CPN-004：threshold 为 0 的券是无门槛券，任意商品金额都可使用。"""
    order = _order(subtotal="50.00")
    coupon = _coupon(threshold=Decimal("0.00"), value=Decimal("0.9"))
    assert apply_coupon(order, coupon) == Decimal("45.00")


def test_discount_coupon_applies_at_exact_threshold() -> None:
    """REQ-CPN-003：商品小计恰好等于门槛时券即可使用。"""
    order = _order(subtotal="100.00")
    coupon = _coupon(threshold=Decimal("100.00"), value=Decimal("0.9"))
    assert apply_coupon(order, coupon) == Decimal("90.00")


def test_no_threshold_coupon_respects_max_discount() -> None:
    """REQ-CPN-004 + REQ-CPN-005：无门槛券同样受 max_discount 封顶。"""
    order = _order(subtotal="1000.00")
    coupon = _coupon(
        threshold=Decimal("0.00"),
        value=Decimal("0.5"),
        max_discount=Decimal("100.00"),
    )
    assert apply_coupon(order, coupon) == Decimal("100.00")


def test_no_threshold_coupon_base_excludes_freight() -> None:
    """REQ-CPN-006：无门槛券的减免基数同样只含商品小计。"""
    order = _order(subtotal="100.00", freight="30.00")
    coupon = _coupon(threshold=Decimal("0.00"), value=Decimal("0.9"))
    assert apply_coupon(order, coupon) == Decimal("90.00")


def test_no_threshold_coupon_in_settlement() -> None:
    """REQ-CPN-004：无门槛券可完成整单结算。"""
    order = _order(subtotal="80.00", freight="10.00")
    coupon = _coupon(threshold=Decimal("0.00"), value=Decimal("0.75"))
    result = settle(order, coupon)
    assert result.discount == Decimal("60.00")
    assert result.payable == Decimal("30.00")
