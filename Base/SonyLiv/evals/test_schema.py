#!/usr/bin/env python3
"""
Schema-conformance tests against src/migrationv2/migrations/*.sql (the
finalized, deployed schema). Runs before any value-level test — if a serving
table exists but with the wrong columns, the query-level tests would fail
with a confusing SQL error instead of a clear "missing column X" message.
This catches that early.

Per table: SKIP if it doesn't exist yet, FAIL if it exists but is missing a
migration-specified column, PASS otherwise. Extra columns are fine (not
checked) — only "the migration's columns are all present" is enforced.
"""
import sys

from ch_client import query, table_exists
from table_names import (
    CC_DELTA_CONTENT, SESSION_ACTIVE, PIPELINE_CHECKPOINT,
    CONTENT_DIM_IMPL, EVENTS_RAW_IMPL,
)

RESULTS = {"pass": 0, "fail": 0, "skip": 0}

# table -> required columns per src/migrationv2/migrations/*.sql DDL
EXPECTED_COLUMNS = {
    EVENTS_RAW_IMPL: {
        "video_session_id", "user_id", "content_id", "event_type", "event",
        "event_ts", "platform", "app_version", "country", "audio_language",
        "subtitle_language", "player_version", "session_start", "ingest_ts",
    },
    CONTENT_DIM_IMPL: {"content_id", "title", "video_type", "category", "updated_at"},
    SESSION_ACTIVE: {
        "video_session_id", "content_id", "platform", "country",
        "video_type", "category", "title", "last_seen", "is_active", "version",
    },
    CC_DELTA_CONTENT: {
        "minute", "content_id", "platform", "country", "video_type",
        "category", "title", "delta_sessions",
    },
    PIPELINE_CHECKPOINT: {"pipeline_name", "checkpoint_ts", "version"},
}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def main():
    for table, expected_cols in EXPECTED_COLUMNS.items():
        name = f"{table} has all LLD-specified columns"
        if not table_exists(table):
            report("skip", name, f"{table} does not exist yet")
            continue
        actual_cols = {r["name"] for r in query(f"DESCRIBE TABLE {table}")}
        missing = expected_cols - actual_cols
        if missing:
            report("fail", name, f"missing columns: {sorted(missing)}")
        else:
            report("pass", name)

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
