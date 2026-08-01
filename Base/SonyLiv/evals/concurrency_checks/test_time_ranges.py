#!/usr/bin/env python3
"""
Check 2 — pick several 5-7 minute time ranges spread across the day (quiet,
ramp-up, near-peak, peak, ramp-down, tail, and one dead zone well outside the
event to check near-zero correctness too) and compare actual vs computed
peak / average concurrency for each, plus report actual distinct
session/user counts for reference (the pipeline has no identity-preserving
serving table to answer distinct counts from — see README.md in this
directory — so those are reported, not compared).
"""
import datetime
import sys

from common import (
    EVENT_DAY, load_actual_intervals, actual_range_metrics,
    computed_peak_avg, within_tol, report, print_summary, RESULTS,
)
from ch_client import table_ready
from table_names import CC_DELTA_CONTENT

UTC = datetime.timezone.utc


def dt(h, m):
    return datetime.datetime(EVENT_DAY.year, EVENT_DAY.month, EVENT_DAY.day, h, m, tzinfo=UTC)


WINDOWS = [
    ("quiet_pre_event", dt(9, 10), dt(9, 16)),     # 6 min, before the event ramps up
    ("ramp_up", dt(10, 31), dt(10, 37)),           # 6 min, sessions actively activating
    ("near_peak", dt(10, 50), dt(10, 56)),         # 6 min, approaching sustained peak
    ("peak_straddle", dt(10, 55), dt(11, 1)),      # 6 min, straddles the actual peak minute
    ("ramp_down", dt(11, 12), dt(11, 18)),         # 6 min, declining
    ("tail", dt(11, 25), dt(11, 31)),              # 6 min, event winding down
    ("dead_zone", dt(14, 0), dt(14, 6)),           # 6 min, well outside the event — near-zero check
]


def collect():
    """Returns (rows, meta) without printing — reusable by build_report.py."""
    if not table_ready(CC_DELTA_CONTENT):
        return [], {"skip_reason": f"{CC_DELTA_CONTENT} not ready"}
    try:
        intervals = load_actual_intervals()
    except FileNotFoundError:
        return [], {"skip_reason": "reference_intervals.csv missing — run ../reference_intervals.py first"}

    rows = []
    for name, start_dt, end_dt in WINDOWS:
        actual = actual_range_metrics(intervals, start_dt, end_dt)
        computed_peak, computed_avg = computed_peak_avg(start_dt, end_dt, EVENT_DAY)
        peak_ok = within_tol(actual["peak"], computed_peak)
        avg_ok = within_tol(actual["avg"], computed_avg)
        rows.append({
            "range_name": name,
            "window": f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}",
            "span_min": round((end_dt - start_dt).total_seconds() / 60),
            "peak_actual": actual["peak"], "peak_computed": computed_peak,
            "avg_actual": actual["avg"], "avg_computed": round(computed_avg, 2),
            "distinct_sessions_actual": actual["distinct_sessions"],
            "distinct_users_actual": actual["distinct_users"],
            "status": "PASS" if (peak_ok and avg_ok) else "FAIL",
        })
    return rows, {"skip_reason": None}


def main():
    rows, meta = collect()
    if meta.get("skip_reason"):
        report("skip", "time-range checks", meta["skip_reason"])
        print_summary()
        sys.exit(0)

    for r in rows:
        label = f"[{r['range_name']}] {r['window']} ({r['span_min']}min)"
        detail = (f"peak: actual={r['peak_actual']} computed={r['peak_computed']}; "
                  f"avg: actual={r['avg_actual']} computed={r['avg_computed']}; "
                  f"actual distinct_sessions={r['distinct_sessions_actual']} distinct_users={r['distinct_users_actual']} "
                  f"(pipeline has no identity-preserving table to compare these against)")
        report(r["status"].lower(), label, detail)

    print_summary()
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
