# order_items：订单商品明细

`order_items.id` 标识一条商品明细，`order_id` 指向订单，`product_id` 指向商品；`quantity` 是购买件数，`unit_price` 是这一单的成交单价。同一订单可有多条商品明细。计算件数用 `SUM(quantity)`；分析实际成交价应使用明细单价，而不是商品表的标价。把明细与支付、退款同时直接连接时，要留意多对多行数放大。
