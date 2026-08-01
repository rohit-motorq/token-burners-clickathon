"""Env config for the concurrency agent. Loads .env if present, falls back to
the same ClickHouse Cloud defaults used by Base/SonyLiv/evals/ch_client.py."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # explicit path, not cwd-relative search — this module can be imported
    # via `python -m src.agent.*` from the repo root, where load_dotenv()'s
    # default cwd-search never finds src/agent/.env.
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

CH_URL = os.environ.get("CH_URL", "https://mg6ws6jmpr.ap-south-1.aws.clickhouse.cloud:8443")
CH_USER = os.environ.get("CH_USER", "default")
CH_PASS = os.environ.get("CH_PASS", "")
# rohitdevtesting runs the migrationv2 pipeline (src/migrationv2/migrations) —
# the authoritative schema going forward, see INNER_CONTEXT.md.
CH_DATABASE = os.environ.get("CH_DATABASE", "rohitdevtesting")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")

# Base URL a browser (not the LibreChat container) can reach this server at,
# for embedding chart image links in replies. This is loaded client-side by
# the user's browser on the host machine, not proxied by LibreChat's backend
# container — so localhost, not host.docker.internal, is correct here.
AGENT_PUBLIC_BASE_URL = os.environ.get("AGENT_PUBLIC_BASE_URL", "http://localhost:8000")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
