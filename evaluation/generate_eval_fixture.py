"""Generate the frozen multi-table business fixture and 40 gold cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


SEED = 20261017
SCHEMA_VERSION = "orders-eval-v2"
START = date(2025, 1, 1)


def iso_day(offset: int) -> str:
    return (START + timedelta(days=offset)).isoformat()


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE customer_segments(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE regions(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE customers(
            id INTEGER PRIMARY KEY, segment_id INTEGER NOT NULL,
            region_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(segment_id) REFERENCES customer_segments(id),
            FOREIGN KEY(region_id) REFERENCES regions(id)
        );
        CREATE TABLE products(
            id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
            cost_price REAL NOT NULL, list_price REAL NOT NULL
        );
        CREATE TABLE campaigns(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, channel TEXT NOT NULL
        );
        CREATE TABLE orders(
            id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
            campaign_id INTEGER, status TEXT NOT NULL, ordered_at TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );
        CREATE TABLE order_items(
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE payments(
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            status TEXT NOT NULL, amount REAL NOT NULL, paid_at TEXT,
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE refunds(
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            amount REAL NOT NULL, reason TEXT NOT NULL, refunded_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE INDEX idx_orders_customer ON orders(customer_id);
        CREATE INDEX idx_orders_campaign ON orders(campaign_id);
        CREATE INDEX idx_items_order ON order_items(order_id);
        CREATE INDEX idx_payments_order ON payments(order_id);
        CREATE INDEX idx_refunds_order ON refunds(order_id);
        """
    )


def populate(db: sqlite3.Connection) -> dict[str, int]:
    rng = random.Random(SEED)
    segments = [(1, "新客"), (2, "成长客"), (3, "高价值客"), (4, "沉睡客"), (5, "企业客户")]
    regions = [(1, "华东"), (2, "华南"), (3, "华北"), (4, "西南"), (5, "海外")]
    categories = ["办公设备", "家居用品", "数码配件", "户外用品", "食品饮料", "服饰"]
    channels = ["自然流量", "站内活动", "社群投放", "合作渠道"]
    reasons = ["七天无理由", "商品破损", "重复下单", "物流延误"]

    db.executemany("INSERT INTO customer_segments VALUES (?, ?)", segments)
    db.executemany("INSERT INTO regions VALUES (?, ?)", regions)
    products = []
    for product_id in range(1, 51):
        category = categories[(product_id - 1) % len(categories)]
        cost = round(20 + rng.random() * 280, 2)
        products.append((product_id, category, f"{category}-{product_id:03d}", cost, round(cost * (1.25 + rng.random() * 0.75), 2)))
    db.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", products)
    campaigns = [(i, f"活动-{i:02d}", channels[(i - 1) % len(channels)]) for i in range(1, 11)]
    db.executemany("INSERT INTO campaigns VALUES (?, ?, ?)", campaigns)

    customers = []
    for customer_id in range(1, 121):
        customers.append((
            customer_id,
            rng.choices(range(1, 6), weights=[32, 28, 18, 12, 10])[0],
            rng.choices(range(1, 6), weights=[35, 25, 20, 12, 8])[0],
            f"客户-{customer_id:03d}",
            iso_day(rng.randrange(0, 150)),
        ))
    db.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)

    orders = []
    items = []
    payments = []
    refunds = []
    item_id = payment_id = refund_id = 1
    statuses = ["paid", "paid", "paid", "cancelled", "pending", "partially_refunded"]
    for order_id in range(1, 401):
        customer_id = rng.randint(1, 120)
        status = rng.choice(statuses)
        campaign_id = None if rng.random() < 0.28 else rng.randint(1, 10)
        ordered_offset = rng.randrange(0, 181)
        ordered_at = iso_day(ordered_offset)
        orders.append((order_id, customer_id, campaign_id, status, ordered_at))
        order_total = 0.0
        for _ in range(rng.randint(1, 4)):
            product_id, _, _, _, list_price = rng.choice(products)
            quantity = rng.randint(1, 5)
            unit_price = round(list_price * (0.82 + rng.random() * 0.16), 2)
            order_total += quantity * unit_price
            items.append((item_id, order_id, product_id, quantity, unit_price))
            item_id += 1
        if status in {"paid", "partially_refunded"}:
            if rng.random() < 0.15:
                first = round(order_total * 0.45, 2)
                payments.append((payment_id, order_id, "paid", first, iso_day(min(180, ordered_offset + 1))))
                payment_id += 1
                payments.append((payment_id, order_id, "paid", round(order_total - first, 2), iso_day(min(180, ordered_offset + 2))))
                payment_id += 1
            else:
                payments.append((payment_id, order_id, "paid", round(order_total, 2), iso_day(min(180, ordered_offset + 1))))
                payment_id += 1
        if status == "partially_refunded" or (status == "paid" and rng.random() < 0.08):
            refund_amount = round(order_total * (0.18 + rng.random() * 0.45), 2)
            refunds.append((refund_id, order_id, refund_amount, rng.choice(reasons), iso_day(min(180, ordered_offset + 8))))
            refund_id += 1

    db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)
    db.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", items)
    db.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", payments)
    db.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?)", refunds)
    db.commit()
    return {"customers": len(customers), "products": len(products), "campaigns": len(campaigns),
            "orders": len(orders), "order_items": len(items), "payments": len(payments),
            "refunds": len(refunds)}


def build_cases(db: sqlite3.Connection) -> list[dict]:
    def add(case_id: str, question: str, category: str, difficulty: str, sql: str,
            tables: list[str], facts: list[str], sources: list[str]) -> dict:
        cursor = db.execute(sql)
        rows = [list(row) for row in cursor.fetchall()]
        return {
            "id": case_id, "question": question, "category": category,
            "difficulty": difficulty, "required_tables": tables,
            "gold_sql_variants": [sql],
            "gold_result": {"columns": [item[0] for item in cursor.description or []], "rows": rows},
            "required_facts": facts, "gold_sources": sources,
        }

    cases: list[dict] = []
    cases.extend([
        add("biz-basic-01", "一共有多少笔订单？", "basic", "easy", "SELECT COUNT(*) AS order_count FROM orders", ["orders"], ["订单总数"], ["schema_orders"]),
        add("biz-basic-02", "有多少笔订单已经支付成功？", "basic", "easy", "SELECT COUNT(DISTINCT order_id) AS paid_orders FROM payments WHERE status='paid'", ["payments"], ["支付成功订单数"], ["rule_payment_status"]),
        add("biz-basic-03", "今年第一季度的订单数量是多少？", "basic", "easy", "SELECT COUNT(*) AS order_count FROM orders WHERE ordered_at >= '2025-01-01' AND ordered_at < '2025-04-01'", ["orders"], ["时间范围", "订单数"], ["schema_orders"]),
        add("biz-basic-04", "各个订单状态分别有多少笔？", "basic", "easy", "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY status", ["orders"], ["状态分组", "订单数"], ["schema_orders"]),
        add("biz-basic-05", "支付成功的金额总计是多少？", "basic", "easy", "SELECT ROUND(SUM(amount),2) AS paid_amount FROM payments WHERE status='paid'", ["payments"], ["支付金额"], ["rule_payment_status"]),
        add("biz-basic-06", "退款记录一共有多少条？", "basic", "easy", "SELECT COUNT(*) AS refund_count FROM refunds", ["refunds"], ["退款记录数"], ["schema_refunds"]),
        add("biz-basic-07", "客户分群各有多少人？", "basic", "easy", "SELECT s.name, COUNT(*) AS customer_count FROM customers c JOIN customer_segments s ON s.id=c.segment_id GROUP BY s.id,s.name ORDER BY s.id", ["customers", "customer_segments"], ["分群", "客户数"], ["schema_customers"]),
        add("biz-basic-08", "商品品类分别有多少种商品？", "basic", "easy", "SELECT category, COUNT(*) AS product_count FROM products GROUP BY category ORDER BY category", ["products"], ["品类", "商品数"], ["schema_products"]),
    ])
    cases.extend([
        add("biz-join-01", "不同区域的客户订单量如何分布？", "multi_table", "medium", "SELECT r.name, COUNT(o.id) AS order_count FROM regions r JOIN customers c ON c.region_id=r.id LEFT JOIN orders o ON o.customer_id=c.id GROUP BY r.id,r.name ORDER BY r.id", ["regions", "customers", "orders"], ["区域", "订单数"], ["schema_regions", "schema_customers"]),
        add("biz-join-02", "各品类实际卖出了多少件？", "multi_table", "medium", "SELECT p.category, SUM(oi.quantity) AS units FROM products p JOIN order_items oi ON oi.product_id=p.id JOIN orders o ON o.id=oi.order_id WHERE o.status IN ('paid','partially_refunded') GROUP BY p.category ORDER BY p.category", ["products", "order_items", "orders"], ["品类", "销量", "有效订单"], ["schema_products", "schema_order_items"]),
        add("biz-join-03", "每个营销渠道带来的支付金额是多少？", "multi_table", "medium", "SELECT c.channel, ROUND(SUM(p.amount),2) AS paid_amount FROM campaigns c JOIN orders o ON o.campaign_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY c.channel ORDER BY c.channel", ["campaigns", "orders", "payments"], ["渠道", "支付金额"], ["schema_campaigns", "schema_payments"]),
        add("biz-join-04", "高价值客户在各区域的订单数是多少？", "multi_table", "medium", "SELECT r.name, COUNT(o.id) AS order_count FROM regions r JOIN customers c ON c.region_id=r.id JOIN orders o ON o.customer_id=c.id JOIN customer_segments s ON s.id=c.segment_id WHERE s.name='高价值客' GROUP BY r.id,r.name ORDER BY r.id", ["regions", "customers", "customer_segments", "orders"], ["区域", "高价值客", "订单数"], ["schema_customer_segments"]),
        add("biz-join-05", "哪些客户既下过订单又发生过退款？", "multi_table", "medium", "SELECT c.id, c.name, COUNT(DISTINCT o.id) AS order_count, ROUND(SUM(rf.amount),2) AS refund_amount FROM customers c JOIN orders o ON o.customer_id=c.id JOIN refunds rf ON rf.order_id=o.id GROUP BY c.id,c.name ORDER BY c.id LIMIT 20", ["customers", "orders", "refunds"], ["客户", "订单数", "退款金额"], ["schema_refunds"]),
        add("biz-join-06", "每个活动的支付订单数和支付金额是多少？", "multi_table", "medium", "SELECT ca.name, COUNT(DISTINCT p.order_id) AS paid_orders, ROUND(SUM(p.amount),2) AS paid_amount FROM campaigns ca JOIN orders o ON o.campaign_id=ca.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY ca.id,ca.name ORDER BY ca.id", ["campaigns", "orders", "payments"], ["活动", "支付订单", "支付金额"], ["schema_campaigns"]),
        add("biz-join-07", "各客户分群购买商品的件数是多少？", "multi_table", "medium", "SELECT s.name, SUM(oi.quantity) AS units FROM customer_segments s JOIN customers c ON c.segment_id=s.id JOIN orders o ON o.customer_id=c.id JOIN order_items oi ON oi.order_id=o.id WHERE o.status IN ('paid','partially_refunded') GROUP BY s.id,s.name ORDER BY s.id", ["customer_segments", "customers", "orders", "order_items"], ["客户分群", "商品件数"], ["schema_order_items"]),
        add("biz-join-08", "每个区域的退款金额和退款笔数是多少？", "multi_table", "medium", "SELECT r.name, COUNT(rf.id) AS refund_count, ROUND(SUM(rf.amount),2) AS refund_amount FROM regions r JOIN customers c ON c.region_id=r.id JOIN orders o ON o.customer_id=c.id JOIN refunds rf ON rf.order_id=o.id GROUP BY r.id,r.name ORDER BY r.id", ["regions", "customers", "orders", "refunds"], ["区域", "退款笔数", "退款金额"], ["schema_refunds"]),
        add("biz-join-09", "活动订单与非活动订单的支付金额对比？", "multi_table", "medium", "SELECT CASE WHEN o.campaign_id IS NULL THEN '无活动' ELSE '有活动' END AS campaign_type, ROUND(SUM(p.amount),2) AS paid_amount FROM orders o JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY campaign_type ORDER BY campaign_type", ["orders", "payments"], ["活动归属", "支付金额"], ["schema_orders"]),
        add("biz-join-10", "商品成本与成交价的总毛利按品类如何分布？", "multi_table", "hard", "SELECT p.category, ROUND(SUM((oi.unit_price-p.cost_price)*oi.quantity),2) AS gross_profit FROM products p JOIN order_items oi ON oi.product_id=p.id JOIN orders o ON o.id=oi.order_id WHERE o.status IN ('paid','partially_refunded') GROUP BY p.category ORDER BY p.category", ["products", "order_items", "orders"], ["成本", "成交价", "毛利", "品类"], ["schema_products"]),
    ])
    cases.extend([
        add("biz-metric-01", "各月份净收入是多少？", "metric", "hard", "SELECT substr(o.ordered_at,1,7) AS month, ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM orders o JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY month ORDER BY month", ["orders", "payments", "refunds"], ["月份", "净收入", "退款扣除"], ["metric_net_revenue"]),
        add("biz-metric-02", "各区域退款率是多少？", "metric", "hard", "SELECT r.name, ROUND(CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT o.id),0),4) AS refund_rate FROM regions r JOIN customers c ON c.region_id=r.id JOIN orders o ON o.customer_id=c.id LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY r.id,r.name ORDER BY r.id", ["regions", "customers", "orders", "refunds"], ["区域", "退款订单", "订单分母"], ["metric_refund_rate"]),
        add("biz-metric-03", "各客户分群的支付客单价是多少？", "metric", "hard", "SELECT s.name, ROUND(SUM(p.amount)/NULLIF(COUNT(DISTINCT p.order_id),0),2) AS avg_order_value FROM customer_segments s JOIN customers c ON c.segment_id=s.id JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY s.id,s.name ORDER BY s.id", ["customer_segments", "customers", "orders", "payments"], ["客户分群", "支付金额", "支付订单数", "客单价"], ["metric_average_order_value"]),
        add("biz-metric-04", "各品类支付净收入排名？", "metric", "hard", "SELECT p.category, ROUND(SUM(pay.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM products p JOIN order_items oi ON oi.product_id=p.id JOIN orders o ON o.id=oi.order_id JOIN payments pay ON pay.order_id=o.id AND pay.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY p.category ORDER BY net_revenue DESC", ["products", "order_items", "orders", "payments", "refunds"], ["品类", "净收入", "排序"], ["metric_net_revenue"]),
        add("biz-metric-05", "支付成功订单中发生退款的比例？", "metric", "hard", "SELECT ROUND(CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT p.order_id),0),4) AS refund_rate FROM payments p LEFT JOIN refunds rf ON rf.order_id=p.order_id WHERE p.status='paid'", ["payments", "refunds"], ["支付订单分母", "退款订单分子"], ["metric_refund_rate"]),
        add("biz-metric-06", "每个营销渠道的平均支付订单金额？", "metric", "hard", "SELECT ca.channel, ROUND(SUM(p.amount)/NULLIF(COUNT(DISTINCT p.order_id),0),2) AS avg_order_value FROM campaigns ca JOIN orders o ON o.campaign_id=ca.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY ca.channel ORDER BY ca.channel", ["campaigns", "orders", "payments"], ["渠道", "客单价"], ["metric_average_order_value"]),
        add("biz-metric-07", "取消订单金额是否应该计入净收入？请给出排除后的金额。", "metric", "hard", "SELECT ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM orders o JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id WHERE o.status <> 'cancelled'", ["orders", "payments", "refunds"], ["排除取消订单", "净收入"], ["metric_net_revenue", "rule_cancelled_order"]),
        add("biz-metric-08", "按区域比较支付订单的平均成交金额。", "metric", "hard", "SELECT r.name, ROUND(SUM(p.amount)/NULLIF(COUNT(DISTINCT p.order_id),0),2) AS avg_order_value FROM regions r JOIN customers c ON c.region_id=r.id JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY r.id,r.name ORDER BY r.id", ["regions", "customers", "orders", "payments"], ["区域", "支付订单", "平均金额"], ["metric_average_order_value"]),
    ])
    cases.extend([
        add("biz-amb-01", "销售额按业务口径应该看支付金额还是订单明细金额？请按净收入口径给出结果。", "ambiguity", "hard", "SELECT ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM payments p LEFT JOIN refunds rf ON rf.order_id=p.order_id WHERE p.status='paid'", ["payments", "refunds"], ["净收入口径", "退款扣除"], ["metric_net_revenue"]),
        add("biz-amb-02", "订单数按全部创建订单统计，还是按支付成功订单统计？请给支付订单数。", "ambiguity", "hard", "SELECT COUNT(DISTINCT order_id) AS paid_orders FROM payments WHERE status='paid'", ["payments"], ["支付成功订单数"], ["rule_payment_status"]),
        add("biz-amb-03", "退款率是退款金额除以支付金额，还是退款订单除以支付订单？按指标目录口径计算。", "ambiguity", "hard", "SELECT ROUND(CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT p.order_id),0),4) AS refund_rate FROM payments p LEFT JOIN refunds rf ON rf.order_id=p.order_id WHERE p.status='paid'", ["payments", "refunds"], ["退款订单分子", "支付订单分母"], ["metric_refund_rate"]),
        add("biz-amb-04", "毛利不是收入。请按成交价减商品成本，统计各品类毛利。", "ambiguity", "hard", "SELECT p.category, ROUND(SUM((oi.unit_price-p.cost_price)*oi.quantity),2) AS gross_profit FROM products p JOIN order_items oi ON oi.product_id=p.id JOIN orders o ON o.id=oi.order_id WHERE o.status IN ('paid','partially_refunded') GROUP BY p.category ORDER BY p.category", ["products", "order_items", "orders"], ["毛利", "成本", "成交价"], ["schema_products"]),
        add("biz-amb-05", "只看实际付款，不把待支付订单算入客户价值。按分群统计支付金额。", "ambiguity", "hard", "SELECT s.name, ROUND(SUM(p.amount),2) AS paid_amount FROM customer_segments s JOIN customers c ON c.segment_id=s.id JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY s.id,s.name ORDER BY s.id", ["customer_segments", "customers", "orders", "payments"], ["支付状态", "客户分群", "支付金额"], ["rule_payment_status"]),
        add("biz-amb-06", "退款金额不能重复扣除。每个订单的净收入先汇总支付，再减退款。", "ambiguity", "hard", "SELECT o.id, ROUND(COALESCE(pay.paid_amount,0)-COALESCE(rf.refund_amount,0),2) AS net_revenue FROM orders o LEFT JOIN (SELECT order_id,SUM(amount) AS paid_amount FROM payments WHERE status='paid' GROUP BY order_id) pay ON pay.order_id=o.id LEFT JOIN (SELECT order_id,SUM(amount) AS refund_amount FROM refunds GROUP BY order_id) rf ON rf.order_id=o.id ORDER BY o.id LIMIT 50", ["orders", "payments", "refunds"], ["先分订单汇总", "避免重复连接", "净收入"], ["metric_net_revenue"]),
        add("biz-amb-07", "只统计有活动归因的订单，比较活动和自然订单的净收入。", "ambiguity", "hard", "SELECT CASE WHEN o.campaign_id IS NULL THEN '自然订单' ELSE '活动订单' END AS order_type, ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM orders o JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY order_type ORDER BY order_type", ["orders", "payments", "refunds"], ["活动归因", "净收入"], ["metric_net_revenue"]),
        add("biz-amb-08", "客户数量和订单数量不能混淆，统计每个分群的客户数以及支付订单数。", "ambiguity", "hard", "SELECT s.name, COUNT(DISTINCT c.id) AS customer_count, COUNT(DISTINCT CASE WHEN p.status='paid' THEN p.order_id END) AS paid_orders FROM customer_segments s JOIN customers c ON c.segment_id=s.id LEFT JOIN orders o ON o.customer_id=c.id LEFT JOIN payments p ON p.order_id=o.id GROUP BY s.id,s.name ORDER BY s.id", ["customer_segments", "customers", "orders", "payments"], ["客户去重", "支付订单去重"], ["schema_customer_segments"]),
    ])
    cases.extend([
        add("biz-multi-01", "找出华东区域净收入最高的三个商品品类，并说明退款影响。", "multi_step", "hard", "SELECT p.category, ROUND(SUM(pay.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue, ROUND(COALESCE(SUM(rf.amount),0),2) AS refund_amount FROM products p JOIN order_items oi ON oi.product_id=p.id JOIN orders o ON o.id=oi.order_id JOIN customers c ON c.id=o.customer_id JOIN regions r ON r.id=c.region_id JOIN payments pay ON pay.order_id=o.id AND pay.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id WHERE r.name='华东' GROUP BY p.category ORDER BY net_revenue DESC LIMIT 3", ["regions", "customers", "orders", "order_items", "products", "payments", "refunds"], ["华东", "净收入", "退款影响", "Top3"], ["metric_net_revenue"]),
        add("biz-multi-02", "比较高价值客户和成长客户在活动订单中的客单价。", "multi_step", "hard", "SELECT s.name, ROUND(SUM(p.amount)/NULLIF(COUNT(DISTINCT p.order_id),0),2) AS avg_order_value FROM customer_segments s JOIN customers c ON c.segment_id=s.id JOIN orders o ON o.customer_id=c.id AND o.campaign_id IS NOT NULL JOIN payments p ON p.order_id=o.id AND p.status='paid' WHERE s.name IN ('高价值客','成长客') GROUP BY s.id,s.name ORDER BY s.id", ["customer_segments", "customers", "orders", "payments"], ["客户分群", "活动订单", "客单价"], ["metric_average_order_value"]),
        add("biz-multi-03", "按季度比较各营销渠道的净收入，并只保留支付成功订单。", "multi_step", "hard", "SELECT substr(o.ordered_at,1,4)||'-Q'||((CAST(substr(o.ordered_at,6,2) AS INTEGER)-1)/3+1) AS quarter, ca.channel, ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM campaigns ca JOIN orders o ON o.campaign_id=ca.id JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY quarter,ca.channel ORDER BY quarter,ca.channel", ["campaigns", "orders", "payments", "refunds"], ["季度", "渠道", "净收入", "支付成功"], ["metric_net_revenue"]),
        add("biz-multi-04", "筛选退款率高于整体退款率的区域。", "multi_step", "hard", "WITH overall AS (SELECT CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT p.order_id),0) AS rate FROM payments p LEFT JOIN refunds rf ON rf.order_id=p.order_id WHERE p.status='paid') SELECT r.name, ROUND(CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT p.order_id),0),4) AS refund_rate FROM regions r JOIN customers c ON c.region_id=r.id JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY r.id,r.name HAVING refund_rate > (SELECT rate FROM overall) ORDER BY refund_rate DESC", ["regions", "customers", "orders", "payments", "refunds"], ["整体基线", "区域退款率", "比较"], ["metric_refund_rate"]),
        add("biz-multi-05", "找出支付订单最多的客户分群，再列出该分群的净收入。", "multi_step", "hard", "WITH ranked AS (SELECT c.segment_id, COUNT(DISTINCT p.order_id) AS paid_orders FROM customers c JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' GROUP BY c.segment_id ORDER BY paid_orders DESC LIMIT 1) SELECT s.name, ROUND(SUM(p.amount)-COALESCE(SUM(rf.amount),0),2) AS net_revenue FROM ranked x JOIN customer_segments s ON s.id=x.segment_id JOIN customers c ON c.segment_id=s.id JOIN orders o ON o.customer_id=c.id JOIN payments p ON p.order_id=o.id AND p.status='paid' LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY s.id,s.name", ["customer_segments", "customers", "orders", "payments", "refunds"], ["先找最大分群", "净收入"], ["metric_net_revenue"]),
        add("biz-multi-06", "对比有活动与无活动订单的退款率，并给出差异。", "multi_step", "hard", "SELECT CASE WHEN o.campaign_id IS NULL THEN '无活动' ELSE '有活动' END AS campaign_type, ROUND(CAST(COUNT(DISTINCT rf.order_id) AS REAL)/NULLIF(COUNT(DISTINCT o.id),0),4) AS refund_rate FROM orders o LEFT JOIN refunds rf ON rf.order_id=o.id GROUP BY campaign_type ORDER BY campaign_type", ["orders", "refunds"], ["活动类型", "退款率", "订单分母"], ["metric_refund_rate"]),
    ])
    return cases


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "orders_eval_v2.db"
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as db:
        create_schema(db)
        counts = populate(db)
        cases = build_cases(db)
    dataset_path = output_dir.parent / "datasets" / "business_cases_v2.jsonl"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    db_sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "seed": SEED,
        "database": str(db_path.as_posix()),
        "case_file": str(dataset_path.as_posix()),
        "case_count": len(cases),
        "row_counts": counts,
        "database_sha256": db_sha256,
        "dataset_sha256": dataset_sha256,
    }
    (output_dir / "orders_eval_v2.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/fixtures"))
    generate(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
