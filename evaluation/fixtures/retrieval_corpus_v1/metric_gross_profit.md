# 指标：商品毛利

商品明细的毛利按 `(order_items.unit_price - products.cost_price) × order_items.quantity` 计算，汇总到品类、订单或其他维度时对明细结果求和。成交单价来自订单明细，单位成本来自商品表；商品标价 `list_price` 不能替代成交价。毛利与支付净额不同，退款是否调整毛利需要额外定义，不能默认两者相同。
