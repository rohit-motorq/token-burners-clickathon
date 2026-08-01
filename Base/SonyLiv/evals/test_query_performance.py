#!/usr/bin/env python3
"""
Query performance — the graded axis the rest of this suite had zero coverage
of. Problem statement: "Judges will look at what your queries read, not just
how fast they return." This runs representative benchmark queries with a
known query_id, flushes system.query_log cluster-wide, and asserts on
read_rows/read_bytes/query_duration_ms — not just wall-clock time from the
Python side.

Thresholds are deliberately generous (hackathon-scale service, network hop
to a Python client) — the point isn't to chase milliseconds, it's to catch
the failure mode the problem statement names explicitly: a "benchmark" query
that secretly does a full-table scan of events_raw or session history instead
of reading a small serving-layer slice.

Every check SKIPs if its table isn't ready yet.
"""
import json
import sys

from ch_client import query_with_stats, table_ready
from table_names import CC_DELTA_DIMS, CC_SI_MINUTE, RAW_EVENTS

RESULTS = {"pass": 0, "fail": 0, "skip": 0}

# A filtered minute-grain serving-layer query has no business reading
# anywhere near the raw event count (905,558 in the training dataset).
# Generous ceiling: a couple thousand rows covers even a full day of
# minute-grain deltas across all dims for one platform filter.
MAX_READ_ROWS = 5000
MAX_DURATION_MS = 3000


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def check_query(name, table, sql):
    if not table_ready(table):
        report("skip", name, f"{table} not ready")
        return
    try:
        _, stats = query_with_stats(sql)
    except Exception as e:
        report("fail", name, f"query error: {e}")
        return
    read_rows = stats.get("read_rows")
    duration_ms = stats.get("query_duration_ms")
    if read_rows is None:
        report("fail", name, "no query_log entry found (flush/lookup failed)")
        return
    problems = []
    if read_rows > MAX_READ_ROWS:
        problems.append(f"read_rows={read_rows} > ceiling {MAX_READ_ROWS} (looks like a scan, not a seek)")
    if duration_ms is not None and duration_ms > MAX_DURATION_MS:
        problems.append(f"query_duration_ms={duration_ms} > ceiling {MAX_DURATION_MS}")
    if problems:
        report("fail", name, "; ".join(problems))
    else:
        report("pass", name, f"read_rows={read_rows}, read_bytes={stats.get('read_bytes')}, "
                              f"duration_ms={duration_ms}, wall_ms={stats.get('wall_ms'):.0f}")


def main():
    check_query(
        "peak concurrency, platform filter, 1hr window (cc_delta_dims) — reads a seek not a scan",
        CC_DELTA_DIMS,
        f"""
        SELECT max(cc) FROM (
            SELECT sum(d) OVER (ORDER BY minute) AS cc
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS}
                  WHERE platform = 'ANDROID_PHONE'
                    AND minute >= toDateTime('2026-07-26 10:00:00')
                    AND minute <  toDateTime('2026-07-26 11:00:00')
                  GROUP BY minute)
        )
        """,
    )
    check_query(
        "minute-grain concurrency curve, no filter, full day (cc_delta_dims)",
        CC_DELTA_DIMS,
        f"""
        SELECT minute, sum(d) OVER (ORDER BY minute) AS cc
        FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS}
              WHERE toDate(minute) = '2026-07-26' GROUP BY minute)
        ORDER BY minute
        """,
    )
    check_query(
        "SI merged distinct-session count, platform filter, 1hr window (cc_si_minute)",
        CC_SI_MINUTE,
        f"""
        SELECT uniqCombinedMerge(sessions_state)
        FROM {CC_SI_MINUTE}
        WHERE platform = 'ANDROID_PHONE'
          AND minute >= toDateTime('2026-07-26 10:00:00')
          AND minute <  toDateTime('2026-07-26 11:00:00')
        """,
    )
    # Negative control: this is what "recomputing from raw history" looks
    # like. It should read close to the full events_raw row count — proving
    # our thresholds actually distinguish scan from seek, not just always pass.
    name = "(control) naive raw-scan query reads ~all of the raw table — sanity check on the ceiling itself"
    if table_ready(RAW_EVENTS):
        _, stats = query_with_stats(f"""
            SELECT count(DISTINCT video_session_id) FROM {RAW_EVENTS}
        """)
        read_rows = stats.get("read_rows")
        if read_rows and read_rows > MAX_READ_ROWS:
            report("pass", name, f"read_rows={read_rows} — confirms MAX_READ_ROWS={MAX_READ_ROWS} would catch a real scan")
        else:
            report("fail", name, f"read_rows={read_rows} — control didn't scan as expected, thresholds may be miscalibrated")
    else:
        report("skip", name, f"{RAW_EVENTS} not ready")

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
