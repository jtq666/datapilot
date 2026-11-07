#!/usr/bin/env bash
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE EXTENSION vector;
CREATE ROLE datapilot_app LOGIN PASSWORD :'app_password';
REVOKE CONNECT ON DATABASE datapilot_app FROM PUBLIC;
GRANT CONNECT ON DATABASE datapilot_app TO datapilot_app;
GRANT USAGE, CREATE ON SCHEMA public TO datapilot_app;
SQL
