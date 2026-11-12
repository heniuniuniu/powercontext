# SPEC-ORDER-014 订单列表状态筛选（当前生效版本 v2）

状态：生效
生效日期：2026-11-12
替代：SPEC-ORDER-009（v1，仅支持按时间范围筛选）

## 需求

订单列表接口 `list_orders(orders, page, page_size, status=None)` 增加可选 `status` 筛选：

1. `status` 为 `None` 时，行为与 v1 完全一致：返回全部订单。
2. `status` 为字符串时，仅返回 `order["status"] == status` 的订单。
3. **必须先过滤再分页**：分页窗口基于过滤后的结果集计算，不能先切片再过滤。
4. 排序规则保持 `created_at` 降序；`created_at` 相同的按 `id` 升序，保证结果稳定。
5. `page` 从 1 开始；超出范围返回空列表，不抛异常。

## 验收口径

- 新增功能测试 `test_status_filter`、`test_status_filter_before_pagination`。
- 原有回归测试 `test_pagination_unchanged`、`test_sort_order_unchanged`、
  `test_out_of_range_returns_empty` 必须继续通过。
- 测试集在修改前后保持一致，不得删除断言或改预期值来"通过"。
