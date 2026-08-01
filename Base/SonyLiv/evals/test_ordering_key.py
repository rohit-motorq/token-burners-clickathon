#!/usr/bin/env python3
"""
Design-quality check: does the actual ORDER BY / engine match what
src/migrationv2/migrations/*.sql specifies? Column presence (test_schema.py)
isn't enough — ORDER BY column ORDER is the index. v2 leads cc_delta_content
with minute (not content_id) since the dims-only rollup table was dropped
and this table now serves both the drill-down and the dashboard-default
query shape by itself. A rename or a reordering during implementation would
silently break every "reads ~5 rows" claim without touching a single column
name.

ClickHouse Cloud reports "Shared*" engine variants (compute-storage
separation transparently substitutes SharedMergeTree for MergeTree, etc.) —
checked mod that prefix, not exact string match.
"""
import sys

from ch_client import query, table_exists
from table_names import (
    CC_DELTA_CONTENT, SESSION_ACTIVE, PIPELINE_CHECKPOINT,
    CONTENT_DIM_IMPL, EVENTS_RAW_IMPL,
)

RESULTS = {"pass": 0, "fail": 0, "skip": 0}

# table -> (expected ORDER BY tuple, expected base engine) per src/migrationv2/migrations/*.sql DDL
EXPECTED = {
    EVENTS_RAW_IMPL: (("video_session_id", "event_ts", "event_type", "event"), "ReplacingMergeTree"),
    CONTENT_DIM_IMPL: (("content_id",), "ReplacingMergeTree"),
    SESSION_ACTIVE: (("last_seen", "video_session_id"), "ReplacingMergeTree"),
    CC_DELTA_CONTENT: (("minute", "content_id", "platform", "country", "video_type", "category"), "SummingMergeTree"),
    PIPELINE_CHECKPOINT: (("pipeline_name",), "ReplacingMergeTree"),
}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def main():
    for table, (expected_order, expected_engine) in EXPECTED.items():
        name = f"{table}: ORDER BY and engine match LLD §2"
        if not table_exists(table):
            report("skip", name, f"{table} does not exist yet")
            continue
        rows = query(f"""
            SELECT engine, sorting_key
            FROM system.tables
            WHERE database = currentDatabase() AND name = '{table}'
        """)
        if not rows:
            report("fail", name, "no row in system.tables — table dropped mid-run?")
            continue
        engine = rows[0]["engine"]
        sorting_key = rows[0]["sorting_key"]
        actual_order = tuple(c.strip() for c in sorting_key.split(",")) if sorting_key else ()

        problems = []
        # ClickHouse Cloud prefixes shared-storage engines with "Shared" — strip it, then compare exactly
        engine_base = engine.replace("Shared", "")
        if engine_base != expected_engine:
            problems.append(f"engine={engine} (base={engine_base}), expected {expected_engine}")
        if actual_order != expected_order:
            problems.append(f"ORDER BY={actual_order}, expected {expected_order} (order matters — it's the index)")

        if problems:
            report("fail", name, "; ".join(problems))
        else:
            report("pass", name, f"engine={engine}, ORDER BY={actual_order}")

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
