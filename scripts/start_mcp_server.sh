#!/usr/bin/env bash
# Runs in the foreground — use start_all.sh to background both servers.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec .venv/bin/python -m src.mcp_server.server
