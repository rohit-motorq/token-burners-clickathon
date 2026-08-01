#!/usr/bin/env python3
"""
Flat active-interval list, one row per (session, active run), reusing the
fold from reference_concurrency.py. This is the primitive for arbitrary
time-range queries — peak/avg/distinct-user-count for any [start, end)
window and any dimension filter — used to build golden_ranges.json.

Output CSV: session_id, user_id, platform, country, content_id, video_type,
            start_ts_ms, end_ts_ms
"""
import csv
import sys
from collections import defaultdict

from reference_concurrency import fetch_events, fold_session


def build_intervals():
    events = fetch_events()
    by_session = defaultdict(list)
    for e in events:
        by_session[e["sid"]].append(e)

    rows = []
    for sid, evs in by_session.items():
        intervals, dims = fold_session(evs)
        if dims is None:
            continue
        platform, country, content_id, video_type, user_id = dims
        for start_ts, end_ts in intervals:
            rows.append((sid, user_id, platform, country, content_id, video_type, start_ts, end_ts))
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session_id", "user_id", "platform", "country", "content_id",
                     "video_type", "start_ts_ms", "end_ts_ms"])
        w.writerows(rows)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "reference_intervals.csv"
    rows = build_intervals()
    write_csv(rows, out)
    print(f"wrote {len(rows)} active intervals -> {out}")
