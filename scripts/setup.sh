#!/usr/bin/env bash
# One-time setup: venv, deps, .env scaffolding. Safe to re-run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV=.venv
if [ ! -d "$VENV" ]; then
    echo "Creating venv at $VENV..."
    python3 -m venv "$VENV"
fi

echo "Installing dependencies..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r src/mcp_server/requirements.txt  # pulls in src/agent/requirements.txt too
"$VENV/bin/pip" install -q -r scripts/requirements.txt

if [ ! -f src/agent/.env ]; then
    echo "Creating src/agent/.env from .env.example — fill in CH_PASS and ANTHROPIC_API_KEY before starting."
    cp src/agent/.env.example src/agent/.env
else
    echo "src/agent/.env already exists, leaving it alone."
fi

mkdir -p logs

echo
echo "Setup done. Next steps:"
echo "  1. Edit src/agent/.env — set CH_PASS and ANTHROPIC_API_KEY."
echo "  2. ./scripts/apply_migrations.sh"
echo "  3. ./scripts/start_all.sh"
echo "  4. (optional) ./scripts/wire_librechat.py /path/to/your/LibreChat"
