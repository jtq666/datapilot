# campaigns：营销活动维表

`campaigns.id` 是活动标识，`name` 是活动名称，`channel` 是营销渠道。`orders.campaign_id` 指向活动，允许为空。看活动渠道带来的付款，需要经 `campaigns → orders → payments`，并在支付流水上过滤成功状态。没有活动 ID 的订单在这份样例中可单独归为非活动订单，不能凭客户来源推断活动归属。
