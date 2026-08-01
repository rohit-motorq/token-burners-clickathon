#!/usr/bin/env python3
"""
Shared helpers for the concurrency_checks sub-suite. Reuses the parent
evals/ infra (ch_client, table_names, reference_intervals.csv) rather than
duplicating it — computation only ever needs cc_delta_content (the delta
table); everything else in the pipeline (session_active, cc_delta_raw,
pipeline_checkpoint) is internal plumbing for how deltas get produced, not
part of the read path.
"""
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ch_client import query, table_ready  # noqa: E402
from table_names import CC_DELTA_CONTENT  # noqa: E402

UTC = datetime.timezone.utc
EVENT_DAY = datetime.date(2026, 7, 26)
REFERENCE_INTERVALS_CSV = os.path.join(os.path.dirname(__file__), "..", "reference_intervals.csv")

RESULTS = {"pass": 0, "fail": 0, "skip": 0}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def print_summary():
    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")


def within_tol(actual, computed, rel=0.03, floor=3):
    if actual is None or computed is None:
        return False
    return abs(computed - actual) <= max(floor, rel * max(actual, 1))


def load_actual_intervals(path=REFERENCE_INTERVALS_CSV):
    """(session_id, user_id, start_ms, end_ms) — ground truth, folded from raw
    events in Python (reference_intervals.py), independent of the SQL pipeline."""
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append((r["session_id"], r["user_id"], int(r["start_ts_ms"]), int(r["end_ts_ms"])))
    return rows


def actual_concurrency_at(intervals, minute_dt):
    """Ground-truth concurrent-session count at an exact minute mark."""
    m_ms = int(minute_dt.timestamp() * 1000)
    return sum(1 for _, _, s, e in intervals if s <= m_ms < e)


def actual_per_minute_sweep(intervals, day=EVENT_DAY):
    """Ground-truth concurrent-session count at every minute of `day`, via a
    sweep line (O(n log n)) rather than an O(minutes x intervals) scan —
    matters once you're covering a full 1440-minute day."""
    day_start = datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_start_ms = int(day_start.timestamp() * 1000)
    day_end_ms = day_start_ms + 24 * 60 * 60 * 1000

    events = []
    for _, _, s, e in intervals:
        if e <= day_start_ms or s >= day_end_ms:
            continue
        events.append((max(s, day_start_ms), 1))
        events.append((min(e, day_end_ms), -1))
    events.sort()

    per_minute = {}
    running = 0
    ei = 0
    for i in range(24 * 60):
        minute_ms = day_start_ms + i * 60_000
        while ei < len(events) and events[ei][0] <= minute_ms:
            running += events[ei][1]
            ei += 1
        minute_dt = day_start + datetime.timedelta(minutes=i)
        per_minute[minute_dt.strftime("%Y-%m-%d %H:%M:%S")] = running
    return per_minute


def computed_concurrency_at(minute_dt, day=EVENT_DAY):
    """Pipeline (cc_delta_content) concurrent-session count at an exact
    minute mark — running sum seeded from the start of the day, then read
    the one minute we care about. Seeding from the window/query start
    instead of the day start silently drops whatever concurrency was
    already carried in from earlier (see Docs/CONCURRENCY_VALIDATION.md
    Finding 1) — always seed from day start."""
    rows = query(f"""
        SELECT sum(d) AS cc FROM (
            SELECT minute, sum(delta_sessions) AS d
            FROM {CC_DELTA_CONTENT}
            WHERE toDate(minute) = '{day.isoformat()}' AND minute <= toDateTime('{minute_dt.strftime("%Y-%m-%d %H:%M:%S")}')
            GROUP BY minute
        )
    """)
    return int(rows[0]["cc"]) if rows and rows[0]["cc"] is not None else 0


def computed_per_minute_series(start_dt, end_dt, day=EVENT_DAY):
    """Pipeline per-minute concurrency for [start_dt, end_dt), day-seeded."""
    day_start = datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + datetime.timedelta(days=1)
    rows = query(f"""
        WITH stepped AS (
            SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent_viewers
            FROM (
                SELECT minute, sum(delta_sessions) AS d
                FROM {CC_DELTA_CONTENT}
                WHERE toDate(minute) = '{day.isoformat()}'
                GROUP BY minute
                ORDER BY minute WITH FILL
                    FROM toDateTime('{day_start.strftime("%Y-%m-%d %H:%M:%S")}')
                    TO   toDateTime('{day_end.strftime("%Y-%m-%d %H:%M:%S")}')
                    STEP INTERVAL 1 MINUTE
            )
        )
        SELECT minute, concurrent_viewers FROM stepped
        WHERE minute >= toDateTime('{start_dt.strftime("%Y-%m-%d %H:%M:%S")}')
          AND minute <  toDateTime('{end_dt.strftime("%Y-%m-%d %H:%M:%S")}')
        ORDER BY minute
    """)
    return {r["minute"]: int(r["concurrent_viewers"]) for r in rows}


def computed_peak_avg(start_dt, end_dt, day=EVENT_DAY):
    """Pipeline peak + time-weighted-avg concurrency over [start_dt, end_dt),
    day-seeded running sum (same query shape as test_range_queries.py's
    peak_avg_from_delta_dims, pointed at cc_delta_content directly)."""
    day_start = datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    start_sql = f"toDateTime('{start_dt.strftime('%Y-%m-%d %H:%M:%S')}')"
    end_sql = f"toDateTime('{end_dt.strftime('%Y-%m-%d %H:%M:%S')}')"
    rows = query(f"""
        WITH stepped AS (
            SELECT
                minute,
                sum(d) OVER (ORDER BY minute) AS cc,
                leadInFrame(minute, 1, {end_sql}) OVER (ORDER BY minute ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) AS nxt
            FROM (
                SELECT minute, sum(delta_sessions) AS d
                FROM {CC_DELTA_CONTENT}
                WHERE toDate(minute) = '{day.isoformat()}'
                GROUP BY minute
            )
        )
        SELECT
            maxIf(cc, minute >= {start_sql} AND minute < {end_sql}) AS peak,
            sum(if(nxt > minute AND minute < {end_sql} AND greatest(minute, {start_sql}) < least(nxt, {end_sql}),
                    cc * (dateDiff('millisecond', greatest(minute, {start_sql}), least(nxt, {end_sql}))), 0))
              / ({end_ms} - {start_ms}) AS avg_cc
        FROM stepped
    """)
    if not rows:
        return None, None
    return rows[0].get("peak"), rows[0].get("avg_cc")


def actual_range_metrics(intervals, start_dt, end_dt):
    """Ground-truth peak / time-weighted-avg / distinct-session / distinct-user
    for [start_dt, end_dt) — no SQL, pure Python, straight off the interval list."""
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    overlapping = [(sid, uid, s, e) for sid, uid, s, e in intervals if s < end_ms and e > start_ms]

    events = []
    for _, _, s, e in overlapping:
        cs, ce = max(s, start_ms), min(e, end_ms)
        if ce <= cs:
            continue
        events.append((cs, 1))
        events.append((ce, -1))
    events.sort()

    peak = running = weighted_sum = 0
    prev_ts = start_ms
    for ts, delta in events:
        weighted_sum += running * (ts - prev_ts)
        running += delta
        peak = max(peak, running)
        prev_ts = ts
    weighted_sum += running * (end_ms - prev_ts)
    avg = weighted_sum / (end_ms - start_ms) if end_ms > start_ms else 0

    return {
        "peak": peak,
        "avg": round(avg, 3),
        "distinct_sessions": len({sid for sid, _, s, e in overlapping}),
        "distinct_users": len({uid for _, uid, s, e in overlapping}),
    }
