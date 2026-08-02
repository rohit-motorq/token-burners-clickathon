#!/usr/bin/env bash
# Thin wrapper so the venv doesn't need to be activated manually.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
.venv/bin/python scripts/apply_migrations.py "$@"
