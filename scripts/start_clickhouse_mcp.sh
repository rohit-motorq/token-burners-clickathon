#!/usr/bin/env bash
# Starts the official ClickHouse MCP server (mcp-clickhouse), read-only,
# as a secondary fallback alongside our own sonyliv-concurrency MCP server.
# Runs in the foreground — use start_all.sh to background all three servers.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE=src/mcp_server/clickhouse_mcp.env
if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE — copy src/mcp_server/clickhouse_mcp.env.example and fill in CLICKHOUSE_PASSWORD."
    exit 1
fi
set -a
source "$ENV_FILE"
set +a
exec .venv/bin/mcp-clickhouse
