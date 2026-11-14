"""回归测试：本次修改**不得**改变以下既有行为。

这些用例在修改前后都必须通过。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from demo_app.models import LineItem, Order
from demo_app.pricing import (
    Coupon,
    CouponKind,
    CouponNotApplicable,
    apply_coupon,
    settle,
)


def _order(subtotal: str = "100.00", freight: str = "0.00", order_id: str = "SO-1") -> Order:
    return Order(
        order_id=order_id,
        items=(LineItem("SKU-1", "商品", Decimal(subtotal), 1),),
        freight=Decimal(freight),
    )


def _coupon(**kwargs) -> Coupon:
    params = {
        "coupon_id": "CPN-1",
        "kind": CouponKind.DISCOUNT,
        "threshold": Decimal("50.00"),
        "value": Decimal("0.9"),
        "max_discount": Decimal("1000.00"),
    }
    params.update(kwargs)
    return Coupon(**params)


def test_discount_coupon_rejected_when_below_threshold() -> None:
    """未达门槛的订单不可使用券。"""
    order = _order(subtotal="30.00")
    with pytest.raises(CouponNotApplicable):
        apply_coupon(order, _coupon(threshold=Decimal("50.00")))


def test_threshold_coupon_reduces_fixed_amount() -> None:
    """满减券按固定金额减免。"""
    order = _order(subtotal="200.00")
    coupon = _coupon(
        kind=CouponKind.THRESHOLD,
        threshold=Decimal("100.00"),
        value=Decimal("30.00"),
    )
    assert apply_coupon(order, coupon) == Decimal("30.00")


def test_discount_coupon_respects_max_discount() -> None:
    """优惠金额受 max_discount 封顶。"""
    order = _order(subtotal="1000.00")
    coupon = _coupon(value=Decimal("0.5"), max_discount=Decimal("100.00"))
    assert apply_coupon(order, coupon) == Decimal("100.00")


def test_discount_never_exceeds_subtotal() -> None:
    """优惠金额不得超过商品小计。"""
    order = _order(subtotal="20.00")
    coupon = _coupon(
        kind=CouponKind.THRESHOLD,
        threshold=Decimal("10.00"),
        value=Decimal("50.00"),
    )
    assert apply_coupon(order, coupon) == Decimal("20.00")


def test_freight_excluded_from_coupon_base() -> None:
    """运费不参与优惠计算。"""
    order = _order(subtotal="100.00", freight="20.00")
    coupon = _coupon(value=Decimal("0.9"), max_discount=Decimal("1000.00"))
    # 只对 100 的商品小计减免，运费 20 不参与。
    assert apply_coupon(order, coupon) == Decimal("90.00")


def test_settlement_total_includes_freight() -> None:
    """应付金额包含运费。"""
    order = _order(subtotal="100.00", freight="12.50")
    coupon = _coupon(value=Decimal("0.9"))
    result = settle(order, coupon)
    assert result.subtotal == Decimal("100.00")
    assert result.discount == Decimal("90.00")
    assert result.freight == Decimal("12.50")
    assert result.payable == Decimal("22.50")


def test_settlement_without_coupon() -> None:
    """不使用优惠券时优惠金额为 0。"""
    order = _order(subtotal="100.00", freight="5.00")
    result = settle(order)
    assert result.discount == Decimal("0.00")
    assert result.payable == Decimal("105.00")


def test_not_applicable_error_mentions_coupon_id() -> None:
    """异常信息包含券编号。"""
    order = _order(subtotal="10.00")
    with pytest.raises(CouponNotApplicable) as excinfo:
        apply_coupon(order, _coupon(coupon_id="CPN-XYZ", threshold=Decimal("50.00")))
    assert "CPN-XYZ" in str(excinfo.value)


def test_negative_threshold_coupon_is_rejected() -> None:
    """REQ-CPN-008：负门槛属于券配置错误，必须拒绝（引入无门槛券之前即如此）。"""
    order = _order(subtotal="100.00")
    with pytest.raises(CouponNotApplicable):
        apply_coupon(order, _coupon(threshold=Decimal("-1.00")))
