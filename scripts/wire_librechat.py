#!/usr/bin/env python3
"""Wire this repo's librechat.yaml into an existing LibreChat checkout.

LibreChat is a separate app (not part of this repo) — this script assumes
you already have it cloned and running via its own docker-compose, and just
needs to be told where it lives. It patches (not overwrites) that
checkout's docker-compose.override.yml to bind-mount our librechat.yaml in,
the same way described in src/agent/INNER_CONTEXT.md.

Usage:
    python scripts/wire_librechat.py /path/to/your/LibreChat

What it does:
  - Verifies the given path looks like a LibreChat checkout (has docker-compose.yml)
  - Merges a bind mount for THIS repo's src/librechat/librechat.yaml into
    <librechat_dir>/docker-compose.override.yml (creates the file if absent,
    preserves any existing content otherwise)
  - Prints the exact command to recreate the LibreChat container

What it does NOT do (do these yourself first, see README.md):
  - Install/clone LibreChat itself
  - Start the MCP/agent servers (use scripts/start_all.sh)
  - Fill in src/agent/.env (use scripts/setup.sh, then edit the file)
"""
import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Missing dependency: pip install pyyaml (or re-run scripts/setup.sh)", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUR_LIBRECHAT_YAML = REPO_ROOT / "src" / "librechat" / "librechat.yaml"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("librechat_dir", type=Path, help="Path to your existing LibreChat checkout")
    args = parser.parse_args()

    librechat_dir = args.librechat_dir.resolve()
    if not (librechat_dir / "docker-compose.yml").exists():
        print(f"{librechat_dir} doesn't look like a LibreChat checkout "
              f"(no docker-compose.yml found).", file=sys.stderr)
        sys.exit(1)

    if not OUR_LIBRECHAT_YAML.exists():
        print(f"Missing {OUR_LIBRECHAT_YAML} — this repo is incomplete?", file=sys.stderr)
        sys.exit(1)

    override_path = librechat_dir / "docker-compose.override.yml"
    config = yaml.safe_load(override_path.read_text()) if override_path.exists() else {}
    config = config or {}
    config.setdefault("services", {})
    config["services"].setdefault("api", {})
    config["services"]["api"].setdefault("volumes", [])

    mount = {
        "type": "bind",
        "source": str(OUR_LIBRECHAT_YAML),
        "target": "/app/librechat.yaml",
        "read_only": True,
    }
    volumes = config["services"]["api"]["volumes"]
    # replace any existing mount targeting the same path, rather than duplicate
    volumes[:] = [v for v in volumes if not (isinstance(v, dict) and v.get("target") == "/app/librechat.yaml")]
    volumes.append(mount)

    override_path.write_text(yaml.safe_dump(config, sort_keys=False))
    print(f"Updated {override_path}")
    print()
    print("Next: recreate the LibreChat container to pick this up —")
    print(f"  cd {librechat_dir} && docker compose up -d --force-recreate api")
    print()
    print("Then check it connected (look for '[MCP] Initialized with 1 configured "
          "server and 7 tools'):")
    print(f"  docker logs LibreChat --tail 50 2>&1 | grep -iE 'mcp|sonyliv'")


if __name__ == "__main__":
    main()
