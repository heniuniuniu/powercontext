"""订单领域模型。

本模块只描述订单与订单行，不包含任何优惠规则；优惠规则位于
``demo_app.pricing``，规格来源见 ``specs/coupon-rules.md``。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    """订单行。"""

    sku: str
    name: str
    unit_price: Decimal
    quantity: int

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
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
