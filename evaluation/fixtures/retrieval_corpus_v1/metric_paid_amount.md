# 指标：成功支付金额

此金额是 `payments` 中 `status='paid'` 的 `amount` 之和，记录的是实际成功收款流水。待支付或失败状态不计入。若同一订单有多条成功流水，金额要全部相加；但统计成功付款的订单笔数时只数不同的 `order_id`。不要用 `order_items.unit_price × quantity` 直接替代收款额，因为成交明细和资金流水是不同事实。
