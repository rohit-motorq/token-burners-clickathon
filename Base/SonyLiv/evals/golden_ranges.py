#!/usr/bin/env python3
"""
Picks concrete time ranges out of the actual dataset (ramp-up, peak,
ramp-down, quiet period, full event window) and computes their expected
foreground active-session / active-user counts from the independent
reference (range_metrics.py over reference_intervals.csv).

This is the golden fixture: test_range_queries.py runs the SAME ranges
through the implementation's serving tables and diffs against these numbers.

Run after reference_intervals.py. Writes golden_ranges.json.
"""
import datetime
import json
import sys

from range_metrics import load_intervals, range_metrics

UTC = datetime.timezone.utc


def ts_ms(y, mo, d, h, mi, s=0):
    return int(datetime.datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp() * 1000)


# Ranges chosen from the EDA's own event-day timeline (§13.5): ramp-up starts
# 10:30, sustained peak 10:47-11:07, decline from 11:10, event ends ~11:30.
RANGE_DEFS = [
    {"name": "quiet_pre_event",        "start": ts_ms(2026, 7, 26, 9, 0),  "end": ts_ms(2026, 7, 26, 9, 30)},
    {"name": "ramp_up",                "start": ts_ms(2026, 7, 26, 10, 30), "end": ts_ms(2026, 7, 26, 10, 40)},
    {"name": "sustained_peak",         "start": ts_ms(2026, 7, 26, 10, 47), "end": ts_ms(2026, 7, 26, 11, 7)},
    {"name": "single_peak_minute",     "start": ts_ms(2026, 7, 26, 10, 55), "end": ts_ms(2026, 7, 26, 10, 56)},
    {"name": "ramp_down",              "start": ts_ms(2026, 7, 26, 11, 10), "end": ts_ms(2026, 7, 26, 11, 30)},
    {"name": "full_event_hour",        "start": ts_ms(2026, 7, 26, 10, 0),  "end": ts_ms(2026, 7, 26, 11, 0)},
    {"name": "full_event_day",         "start": ts_ms(2026, 7, 26, 0, 0),   "end": ts_ms(2026, 7, 27, 0, 0)},
]

FILTER_DEFS = [
    {"name": "no_filter", "filters": {}},
    {"name": "platform_android_phone", "filters": {"platform": "ANDROID_PHONE"}},
    {"name": "platform_iphone", "filters": {"platform": "IPHONE"}},
    {"name": "video_type_live", "filters": {"video_type": "Live"}},
]


def main():
    intervals = load_intervals()
    golden = []
    for rd in RANGE_DEFS:
        for fd in FILTER_DEFS:
            m = range_metrics(intervals, rd["start"], rd["end"], fd["filters"])
            golden.append({
                "range_name": rd["name"],
                "filter_name": fd["name"],
                "start_ts_ms": rd["start"],
                "end_ts_ms": rd["end"],
                "filters": fd["filters"],
                **m,
            })
    out = sys.argv[1] if len(sys.argv) > 1 else "golden_ranges.json"
    with open(out, "w") as f:
        json.dump(golden, f, indent=2)
    print(f"wrote {len(golden)} golden range/filter combos -> {out}")
    for g in golden:
        if g["filter_name"] == "no_filter":
            print(f"  {g['range_name']:20s} peak={g['peak_concurrency']:5d} "
                  f"avg={g['avg_concurrency']:8.2f} "
                  f"sessions={g['distinct_active_sessions']:5d} "
                  f"users={g['distinct_active_users']:5d}")


if __name__ == "__main__":
    main()
