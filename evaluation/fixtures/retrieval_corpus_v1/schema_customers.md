# customers：客户主表

`customers.id` 是客户标识，`name` 是显示名称，`created_at` 记录建档时间。`segment_id` 指向 `customer_segments.id`，`region_id` 指向 `regions.id`。一位客户可以有多笔订单，`orders.customer_id` 指回本表。需要同时观察客群和区域时，从本表分别连接两个维表，再连接订单；客户数应按客户 ID 去重，不能把订单行数当人数。
