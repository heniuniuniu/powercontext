"""订单优惠结算规则。

规则来源见 ``specs/coupon-rules.md``，接口契约见 ``specs/order-settlement-api.md``。
本模块的对外入口是 :func:`apply_coupon` 与 :func:`settle`。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from .models import Order


class CouponNotApplicable(Exception):
    """优惠券不适用于该订单。"""


class CouponKind(str, Enum):
    """券类型。"""

    THRESHOLD = "threshold"  # 满减券：满 threshold 减 value
    DISCOUNT = "discount"  # 折扣券：按 value 比例减免


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class SettlementResult:
    """结算结果。"""

    subtotal: Decimal
    discount: Decimal
    freight: Decimal
    payable: Decimal


def _round_money(value: Decimal) -> Decimal:
    """金额统一保留两位小数，四舍五入。"""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
