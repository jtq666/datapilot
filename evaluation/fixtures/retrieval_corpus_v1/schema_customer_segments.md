# customer_segments：客户分群维表

`customer_segments.id` 是分群标识，`name` 是分群名称；样例中包括新客、成长客、高价值客、沉睡客和企业客户。`customers.segment_id` 与它关联。统计某类客户人数时先按客户 ID 计数；统计该类客户的支付订单，还需经 `customers → orders → payments`，并按订单 ID 去重。
