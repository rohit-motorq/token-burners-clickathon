#!/usr/bin/env python3
"""
Range-level metrics computed directly from reference_intervals.csv — the
independent ground truth. No SQL, no reuse of pipeline logic.

For a [start_ts, end_ts) window (ms epoch) and an optional dimension filter,
computes:
  peak_concurrency       max simultaneous active sessions at any minute in range
  avg_concurrency        time-weighted average active sessions across range
  distinct_active_sessions  sessions with any overlap with the range
  distinct_active_users     unique users with any overlap with the range
"""
import csv
from bisect import bisect_right
from collections import defaultdict

MINUTE_MS = 60_000


def load_intervals(path="reference_intervals.csv"):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "session_id": r["session_id"],
                "user_id": r["user_id"],
                "platform": r["platform"],
                "country": r["country"],
                "content_id": r["content_id"],
                "video_type": r["video_type"],
                "start_ts": int(r["start_ts_ms"]),
                "end_ts": int(r["end_ts_ms"]),
            })
    return rows


def _matches(row, filters):
    def one(k, v):
        if isinstance(v, (list, set, tuple)):
            return row[k] in v
        return row[k] == v
    return all(one(k, v) for k, v in filters.items())


def range_metrics(intervals, start_ts, end_ts, filters=None):
    filters = filters or {}
    overlapping = [
        r for r in intervals
        if r["start_ts"] < end_ts and r["end_ts"] > start_ts and _matches(r, filters)
    ]

    distinct_sessions = len({r["session_id"] for r in overlapping})
    distinct_users = len({r["user_id"] for r in overlapping})

    # minute-grain sweep for peak + time-weighted avg, clipped to [start_ts, end_ts)
    events = []  # (ts, +1/-1)
    for r in overlapping:
        s = max(r["start_ts"], start_ts)
        e = min(r["end_ts"], end_ts)
        if e <= s:
            continue
        events.append((s, 1))
        events.append((e, -1))
    events.sort()

    peak = 0
    running = 0
    weighted_sum = 0
    prev_ts = start_ts
    for ts, delta in events:
        weighted_sum += running * (ts - prev_ts)
        running += delta
        peak = max(peak, running)
        prev_ts = ts
    weighted_sum += running * (end_ts - prev_ts)
    avg = weighted_sum / (end_ts - start_ts) if end_ts > start_ts else 0

    return {
        "peak_concurrency": peak,
        "avg_concurrency": round(avg, 3),
        "distinct_active_sessions": distinct_sessions,
        "distinct_active_users": distinct_users,
    }
