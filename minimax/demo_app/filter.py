"""订单服务：列表查询（修改前快照，v1）。"""


def list_orders(orders, page=1, page_size=10):
    """按 created_at 降序返回第 page 页订单。"""
    rows = sorted(orders, key=lambda o: (-o["created_at"], o["id"]))
    start = (page - 1) * page_size
    return rows[start:start + page_size]
