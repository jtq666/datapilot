# orders：订单主表

`orders.id` 是订单标识，`customer_id` 关联 `customers.id`，可空的 `campaign_id` 关联 `campaigns.id`。`status` 记录订单业务状态，`ordered_at` 是下单时间。订单与 `order_items`、`payments`、`refunds` 都是一对多关系。创建的订单数通常数 `orders.id`；支付成功订单数应根据成功支付流水识别，不能只数订单表行数。
