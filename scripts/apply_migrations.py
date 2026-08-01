#!/usr/bin/env python3
"""Apply .sql migration files against the configured ClickHouse instance.

Reads connection config the same way the agent does (src/agent/config.py —
CH_URL/CH_USER/CH_PASS/CH_DATABASE from src/agent/.env). Every statement is
idempotent (CREATE ... IF NOT EXISTS) so this is safe to re-run.

Usage:
    python scripts/apply_migrations.py                        # applies src/migrationv2/migrations (default, authoritative)
    python scripts/apply_migrations.py --dir src/migrations    # applies the other schema instead

Only use --dir src/migrations if you know you're targeting a different
database than rohitdevtesting — see src/agent/INNER_CONTEXT.md for why
migrationv2 is the authoritative schema.
"""
import argparse
import base64
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agent import config  # noqa: E402


def exec_sql(sql: str) -> None:
    url = config.CH_URL + "?" + urllib.parse.urlencode({"database": config.CH_DATABASE})
    req = urllib.request.Request(url, data=sql.strip().encode(), method="POST")
    auth = f"{config.CH_USER}:{config.CH_PASS}".encode()
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def statements_in(sql_file: Path) -> list[str]:
    # Strip full-line comments BEFORE splitting on ';' — a semicolon inside a
    # comment (plain English prose, e.g. "traffic; content_id is the...")
    # otherwise splits a statement mid-sentence. Bit us once, see
    # src/agent/INNER_CONTEXT.md if curious.
    lines = sql_file.read_text().splitlines()
    code_only = "\n".join(l for l in lines if not l.strip().startswith("--"))
    return [s.strip() for s in code_only.split(";") if s.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="src/migrationv2/migrations",
                         help="Directory of .sql files to apply, in sorted order (default: src/migrationv2/migrations)")
    args = parser.parse_args()

    mig_dir = REPO_ROOT / args.dir
    sql_files = sorted(mig_dir.glob("*.sql"))
    if not sql_files:
        print(f"No .sql files found in {mig_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Applying {len(sql_files)} migration(s) from {mig_dir}")
    print(f"  -> {config.CH_URL} / database={config.CH_DATABASE}")
    if not config.CH_PASS:
        print("CH_PASS is empty — set it in src/agent/.env first.", file=sys.stderr)
        sys.exit(1)

    for sql_file in sql_files:
        stmts = statements_in(sql_file)
        print(f"  {sql_file.name} ({len(stmts)} statement(s))...", end=" ", flush=True)
        try:
            for stmt in stmts:
                exec_sql(stmt)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print("FAILED")
            print(f"    {body.strip()}", file=sys.stderr)
            sys.exit(1)
        print("ok")

    print("All migrations applied.")


if __name__ == "__main__":
    main()
