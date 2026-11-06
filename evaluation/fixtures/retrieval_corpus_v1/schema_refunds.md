# refunds：退款流水

`refunds.id` 标识一条退款记录，`order_id` 指向订单，`amount` 是本次退回金额，`reason` 是原因，`refunded_at` 是退款时间。一笔订单可能发生多次退款。要统计发生过退款的订单数，应按 `order_id` 去重；要统计退款总额，汇总全部相关退款流水。订单状态不能替代退款金额。
