#!/usr/bin/env bash
# Runs in the foreground — use start_all.sh to background both servers.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec .venv/bin/python -m uvicorn src.agent.server:app --host 0.0.0.0 --port 8000
