"""订单列表测试集（修改前后完全一致，不允许删断言或改预期）。"""
import unittest

from demo_app.filter import list_orders

ORDERS = [
    {"id": 1, "status": "paid", "created_at": 100},
    {"id": 2, "status": "pending", "created_at": 90},
    {"id": 3, "status": "paid", "created_at": 80},
    {"id": 4, "status": "cancelled", "created_at": 70},
    {"id": 5, "status": "pending", "created_at": 60},
    {"id": 6, "status": "paid", "created_at": 50},
]


class OrderListTests(unittest.TestCase):
    def test_status_filter(self):
        got = list_orders(ORDERS, page=1, page_size=10, status="paid")
        self.assertEqual([o["id"] for o in got], [1, 3, 6])

    def test_status_filter_before_pagination(self):
        got = list_orders(ORDERS, page=1, page_size=2, status="paid")
        self.assertEqual([o["id"] for o in got], [1, 3])

    def test_pagination_unchanged(self):
        self.assertEqual([o["id"] for o in list_orders(ORDERS, page=2, page_size=2)], [3, 4])

    def test_sort_order_unchanged(self):
        self.assertEqual([o["id"] for o in list_orders(ORDERS, page=1, page_size=6)], [1, 2, 3, 4, 5, 6])

    def test_out_of_range_returns_empty(self):
        self.assertEqual(list_orders(ORDERS, page=99, page_size=2), [])


if __name__ == "__main__":
    unittest.main()
