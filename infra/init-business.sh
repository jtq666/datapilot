#!/usr/bin/env bash
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v reader_password="$BUSINESS_DB_PASSWORD" <<'SQL'
CREATE ROLE datapilot_reader LOGIN PASSWORD :'reader_password';
REVOKE CONNECT, TEMPORARY ON DATABASE datapilot_business FROM PUBLIC;
GRANT CONNECT ON DATABASE datapilot_business TO datapilot_reader;
CREATE SCHEMA analytics;
CREATE TABLE analytics.orders (
    order_id integer PRIMARY KEY, channel text NOT NULL, paid_at date NOT NULL, amount numeric(12,2) NOT NULL
);
CREATE TABLE analytics.refunds (
    refund_id integer PRIMARY KEY, order_id integer NOT NULL REFERENCES analytics.orders,
    status text NOT NULL CHECK (status IN ('succeeded','pending','rejected')),
    amount numeric(12,2) NOT NULL, requested_at date NOT NULL
);
CREATE TABLE analytics.customer_contacts (customer_id integer PRIMARY KEY, phone text NOT NULL);
INSERT INTO analytics.orders
SELECT n, CASE WHEN n <= 4 THEN 'web' ELSE 'app' END,
       date_trunc('month', CURRENT_DATE)::date, 100
FROM generate_series(1, 8) AS n;
INSERT INTO analytics.refunds VALUES
    (1, 1, 'succeeded', 100, date_trunc('month', CURRENT_DATE)::date),
    (2, 2, 'pending', 100, date_trunc('month', CURRENT_DATE)::date),
    (3, 5, 'succeeded', 100, date_trunc('month', CURRENT_DATE)::date),
    (4, 5, 'succeeded', 20, date_trunc('month', CURRENT_DATE)::date);
INSERT INTO analytics.customer_contacts VALUES (1, 'synthetic-demo-only');
GRANT USAGE ON SCHEMA analytics TO datapilot_reader;
GRANT SELECT ON analytics.orders, analytics.refunds TO datapilot_reader;
ALTER ROLE datapilot_reader SET default_transaction_read_only = on;
ALTER ROLE datapilot_reader SET search_path TO pg_catalog, analytics;
SQL
