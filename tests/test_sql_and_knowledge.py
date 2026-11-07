import pytest

from datapilot.knowledge import bm25, fuse, split_document
from datapilot.sql import validated_sql
from datapilot.tools import ToolExecutionError


@pytest.mark.parametrize("query", [
    "DELETE FROM orders", "SELECT * FROM orders; SELECT * FROM refunds",
    "WITH modified AS (DELETE FROM orders RETURNING *) SELECT * FROM modified",
    "SELECT * FROM analytics.customer_contacts", "SELECT * FROM pg_roles",
    "SELECT pg_sleep(1)", "SELECT * FROM orders FOR UPDATE",
    "SELECT * FROM pg_roles WHERE EXISTS (WITH pg_roles AS (SELECT 1) SELECT 1)",
])
def test_sql_rejects_invalid_or_unauthorized(query):
    with pytest.raises(ToolExecutionError):
        validated_sql(query)


def test_sql_supports_analytical_query():
    sql = """
        WITH base AS (SELECT order_id, channel FROM analytics.orders
            WHERE paid_at >= date_trunc('month', CURRENT_DATE))
        SELECT channel, COUNT(DISTINCT b.order_id), COUNT(DISTINCT CASE WHEN r.order_id IS NOT NULL THEN b.order_id END)
        FROM base b LEFT JOIN refunds r ON b.order_id=r.order_id AND r.status='succeeded'
        GROUP BY channel ORDER BY channel
    """
    assert "DATE_TRUNC" in validated_sql(sql)


def test_hybrid_recall_helpers():
    rows = [{"chunk_id": "a", "text": "refund_amount 退款金额"},
            {"chunk_id": "b", "text": "paid_at 支付时间"}]
    assert bm25(rows, "refund_amount") == ["a"]
    assert bm25(rows, "退款") == ["a"]
    assert fuse(["a", "b"], ["b", "c"])[0] == "b"
    assert len(split_document("文" * 1200)) == 2
