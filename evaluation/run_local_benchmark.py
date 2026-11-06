"""Run the deterministic, model-free benchmark used in the project notes.

The benchmark deliberately measures engineering contracts rather than model
quality: hybrid retrieval ranking, read-only SQL execution, and SQL rejection.
It creates its fixture database in a temporary directory and writes only the
summary JSON requested by the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from data_platform.data_sources.sqlite_source import QueryRejected, SQLiteDataSource
from data_platform.knowledge.chunking import search_tokens
from data_platform.knowledge.store import KnowledgeStore


class DeterministicEmbeddings:
    """A local embedding substitute with stable output and no network calls."""

    model = "evaluation-hash-v1"

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 128
        for token in search_tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % len(vector)
            vector[index] += 1.0
        return vector

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def queries(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


RETRIEVAL_CASES = [
    ("metric_net_revenue", "净收入计算公式", "指标-净收入.md", "指标"),
    ("metric_refund_rate", "退款率计算公式", "指标-退款率.md", "指标"),
    ("metric_average_order_value", "客单价计算公式", "指标-客单价.md", "指标"),
    ("metric_paid_orders", "已支付订单定义", "指标-已支付订单.md", "指标"),
    ("schema_orders", "orders 表字段 customer_id", "Schema-orders.md", "Schema"),
    ("schema_order_items", "order_items 表字段 product_id", "Schema-order-items.md", "Schema"),
    ("schema_payments", "payments 表字段 paid_amount", "Schema-payments.md", "Schema"),
    ("schema_customers", "customers 表字段 segment_id", "Schema-customers.md", "Schema"),
    ("rule_refund_window", "付款后七天退款规则", "规则-退款窗口.md", "规则"),
    ("rule_cancelled_order", "已取消订单不计入收入", "规则-取消订单.md", "规则"),
    ("rule_payment_status", "支付成功状态为 paid", "规则-支付状态.md", "规则"),
    ("rule_data_scope", "工作区只能访问自己的数据源", "规则-数据隔离.md", "规则"),
    ("combo_revenue", "净收入和退款率需要支付与退款规则", "组合-收入退款.md", "组合"),
    ("combo_customer_orders", "客户分群与订单关联字段", "组合-客户订单.md", "组合"),
    ("combo_product_sales", "商品销量与订单明细关联", "组合-商品销量.md", "组合"),
    ("combo_payment_orders", "支付金额和订单状态关联", "组合-支付订单.md", "组合"),
]


def run_retrieval_benchmark() -> dict:
    embeddings = DeterministicEmbeddings()
    with tempfile.TemporaryDirectory(prefix="agent-eval-kb-", ignore_cleanup_errors=True) as directory:
        store = KnowledgeStore(Path(directory) / "knowledge.db")
        kb_id = store.create_base("evaluation", "frozen-retrieval-cases")
        for case_id, query, filename, category in RETRIEVAL_CASES:
            text = (
                f"{filename}。测试材料 {case_id}。{query}。"
                f"本材料只用于冻结的 {category} 检索题。"
            )
            store.add_document("evaluation", kb_id, filename, text, embeddings)

        rows = []
        for case_id, query, expected_filename, category in RETRIEVAL_CASES:
            hits = store.search("evaluation", kb_id, query, embeddings, top_k=5, candidate_k=16)
            rank = next(
                (index for index, hit in enumerate(hits, start=1)
                 if hit.filename == expected_filename),
                None,
            )
            rows.append({
                "id": case_id,
                "category": category,
                "expected_filename": expected_filename,
                "rank": rank,
                "hit": rank is not None,
            })

    total = len(rows)
    hit_count = sum(row["hit"] for row in rows)
    return {
        "cases": total,
        "recall_at_5": hit_count / total if total else 0.0,
        "mrr_at_5": sum(1 / row["rank"] for row in rows if row["rank"]) / total if total else 0.0,
        "source_coverage": hit_count / total if total else 0.0,
        "details": rows,
    }


VALID_SQL_CASES = [
    ("count_orders", "SELECT COUNT(*) AS total FROM orders", ["total"], [[4]]),
    ("paid_total", "SELECT SUM(amount) AS total FROM payments WHERE status = 'paid'", ["total"], [[700]]),
    ("customer_order_count", "SELECT customer_id, COUNT(*) AS total FROM orders GROUP BY customer_id ORDER BY customer_id", ["customer_id", "total"], [[1, 2], [2, 1], [3, 1]]),
    ("join_items", "SELECT SUM(oi.quantity) AS total FROM order_items oi JOIN orders o ON o.id = oi.order_id", ["total"], [[7]]),
    ("paid_order_count", "SELECT COUNT(*) AS total FROM orders WHERE status = 'paid'", ["total"], [[3]]),
    ("max_payment", "SELECT MAX(amount) AS total FROM payments", ["total"], [[400]]),
]

BLOCKED_SQL_CASES = [
    ("delete", "DELETE FROM orders"),
    ("update", "UPDATE orders SET status = 'paid'"),
    ("drop", "DROP TABLE orders"),
    ("multi_statement", "SELECT * FROM orders; DELETE FROM orders"),
    ("pragma", "PRAGMA user_version"),
    ("attach", "ATTACH DATABASE 'other.db' AS other"),
]


def create_sql_fixture(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT);
            INSERT INTO orders VALUES (1, 1, 'paid'), (2, 1, 'paid'),
                (3, 2, 'paid'), (4, 3, 'cancelled');
            CREATE TABLE payments(id INTEGER PRIMARY KEY, order_id INTEGER, amount INTEGER, status TEXT);
            INSERT INTO payments VALUES (1, 1, 100, 'paid'), (2, 2, 200, 'paid'),
                (3, 3, 400, 'paid'), (4, 4, 50, 'refunded');
            CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER, quantity INTEGER);
            INSERT INTO order_items VALUES (1, 1, 2), (2, 2, 1), (3, 3, 3), (4, 4, 1);
            """
        )


def run_sql_benchmark() -> dict:
    with tempfile.TemporaryDirectory(prefix="agent-eval-sql-", ignore_cleanup_errors=True) as directory:
        path = Path(directory) / "fixture.db"
        create_sql_fixture(path)
        source = SQLiteDataSource(path)

        valid_results = []
        for case_id, sql, columns, rows in VALID_SQL_CASES:
            result = source.query(sql)
            valid_results.append({
                "id": case_id,
                "correct": result.columns == columns and result.rows == rows,
            })

        blocked_results = []
        for case_id, sql in BLOCKED_SQL_CASES:
            try:
                source.query(sql)
            except QueryRejected:
                blocked_results.append({"id": case_id, "blocked": True})
            else:
                blocked_results.append({"id": case_id, "blocked": False})

    return {
        "valid_cases": len(valid_results),
        "valid_correct": sum(item["correct"] for item in valid_results),
        "valid_result_accuracy": sum(item["correct"] for item in valid_results) / len(valid_results),
        "blocked_cases": len(blocked_results),
        "blocked": sum(item["blocked"] for item in blocked_results),
        "dangerous_sql_block_rate": sum(item["blocked"] for item in blocked_results) / len(blocked_results),
        "valid_details": valid_results,
        "blocked_details": blocked_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path("evaluation/results/local_benchmark.json"),
    )
    args = parser.parse_args()
    report = {
        "benchmark": "local-contract-v1",
        "mode": "deterministic_offline_no_model_calls",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retrieval": run_retrieval_benchmark(),
        "sql": run_sql_benchmark(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "benchmark": report["benchmark"],
        "retrieval": {key: report["retrieval"][key] for key in ("cases", "recall_at_5", "mrr_at_5")},
        "sql": {key: report["sql"][key] for key in ("valid_cases", "valid_correct", "blocked_cases", "blocked")},
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
