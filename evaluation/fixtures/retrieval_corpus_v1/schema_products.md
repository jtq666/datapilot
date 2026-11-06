# products：商品维表

`products.id` 是商品标识，`name` 是名称，`category` 是品类；`cost_price` 是单位成本，`list_price` 是标价。`order_items.product_id` 指向本表。商品销售分析通常结合 `order_items.quantity` 和 `order_items.unit_price`；标价不一定等于成交价，成本也不是支付流水金额。
