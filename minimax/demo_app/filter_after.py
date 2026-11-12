"""订单服务：列表查询（修改后快照，SPEC-ORDER-014 v2）。"""


def list_orders(orders, page=1, page_size=10, status=None):
    """按 created_at 降序返回第 page 页订单；status 不为 None 时先过滤再分页。"""
    rows = sorted(orders, key=lambda o: (-o["created_at"], o["id"]))
    if status is not None:
        rows = [o for o in rows if o["status"] == status]
    start = (page - 1) * page_size
    return rows[start:start + page_size]
