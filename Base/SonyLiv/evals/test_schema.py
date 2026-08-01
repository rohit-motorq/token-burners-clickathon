#!/usr/bin/env python3
"""
Schema-conformance tests against LLD-sam.md §2 (Data model). Runs before any
value-level test — if a serving table exists but with the wrong columns, the
query-level tests would fail with a confusing SQL error instead of a clear
"missing column X" message. This catches that early.

Per table: SKIP if it doesn't exist yet, FAIL if it exists but is missing an
LLD-specified column, PASS otherwise. Extra columns are fine (not checked) —
only "the LLD's columns are all present" is enforced.
"""
import sys

from ch_client import query, table_exists
from table_names import (
    CC_DELTA_CONTENT, CC_DELTA_DIMS, CC_SI_MINUTE, SESSION_STATE,
    SESSION_RUNS, PIPELINE_CURSOR, CONTENT_DIM_IMPL, EVENTS_RAW_IMPL,
)

RESULTS = {"pass": 0, "fail": 0, "skip": 0}

# table -> required columns per LLD §2 DDL
EXPECTED_COLUMNS = {
    EVENTS_RAW_IMPL: {
        "video_session_id", "user_id", "content_id", "event_type", "event",
        "event_ts", "platform", "app_version", "country", "audio_language",
        "subtitle_language", "player_version", "session_start", "ingest_ts",
    },
    CONTENT_DIM_IMPL: {"content_id", "title", "video_type", "category", "updated_at"},
    SESSION_STATE: {
        "video_session_id", "user_id", "content_id", "platform", "country",
        "video_type", "category", "fg", "playing", "ended", "last_seen",
        "open_run_start", "ver",
    },
    CC_DELTA_CONTENT: {
        "minute", "content_id", "platform", "country", "video_type",
        "category", "delta_sessions",
    },
    CC_DELTA_DIMS: {
        "minute", "platform", "country", "video_type", "category", "delta_sessions",
    },
    CC_SI_MINUTE: {
        "minute", "content_id", "platform", "country", "video_type",
        "sessions_state", "users_state",
    },
    SESSION_RUNS: {
        "video_session_id", "run_start", "run_end", "content_id", "platform",
        "country", "video_type", "category", "sign",
    },
    PIPELINE_CURSOR: {"name", "cursor_ts", "ver"},
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
