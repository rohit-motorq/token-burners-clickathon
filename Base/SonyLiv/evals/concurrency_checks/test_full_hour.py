#!/usr/bin/env python3
"""
Check 3 — actual vs computed concurrency for every minute from 10:00:00 to
10:59:00 UTC on the event day (the sustained-peak hour). One PASS/FAIL per
minute plus the single worst-diverging minute, since range-level peak/avg
alone can hide a sharp single-minute spike or dip — see
Docs/CONCURRENCY_VALIDATION.md for a worked example (a +165% spike right at
the 10:30 ramp-up boundary that only shows up minute-by-minute).
"""
import datetime
import sys

from common import (
    EVENT_DAY, load_actual_intervals, actual_concurrency_at,
    computed_per_minute_series, within_tol, report, print_summary, RESULTS,
)
from ch_client import table_ready
from table_names import CC_DELTA_CONTENT

UTC = datetime.timezone.utc
WINDOW_START = datetime.datetime(EVENT_DAY.year, EVENT_DAY.month, EVENT_DAY.day, 10, 0, tzinfo=UTC)
WINDOW_END = datetime.datetime(EVENT_DAY.year, EVENT_DAY.month, EVENT_DAY.day, 10, 59, tzinfo=UTC)


def collect():
    """Returns (rows, meta) without printing — reusable by build_report.py."""
    if not table_ready(CC_DELTA_CONTENT):
        return [], {"skip_reason": f"{CC_DELTA_CONTENT} not ready"}
    try:
        intervals = load_actual_intervals()
    except FileNotFoundError:
        return [], {"skip_reason": "reference_intervals.csv missing — run ../reference_intervals.py first"}

    computed = computed_per_minute_series(WINDOW_START, WINDOW_END + datetime.timedelta(minutes=1), EVENT_DAY)

    rows = []
    worst = None  # (abs_pct, minute, actual, computed)
    n_minutes = int((WINDOW_END - WINDOW_START).total_seconds() // 60) + 1
    for i in range(n_minutes):
        m = WINDOW_START + datetime.timedelta(minutes=i)
        m_str = m.strftime("%Y-%m-%d %H:%M:%S")
        actual = actual_concurrency_at(intervals, m)
        comp = computed.get(m_str, 0)
        diff = comp - actual
        diff_pct = (diff / actual * 100) if actual else (0.0 if diff == 0 else float("inf"))
        rows.append({
            "minute": m_str, "actual": actual, "computed": comp,
            "diff": diff, "diff_pct": diff_pct,
            "status": "PASS" if within_tol(actual, comp) else "FAIL",
        })
        if actual > 0:
            pct = abs(diff) / actual
            if worst is None or pct > worst[0]:
                worst = (pct, m_str, actual, comp)

    return rows, {"skip_reason": None, "worst": worst}


def main():
    rows, meta = collect()
    if meta.get("skip_reason"):
        report("skip", f"{WINDOW_START}-{WINDOW_END} minute series", meta["skip_reason"])
        print_summary()
        sys.exit(0)

    for r in rows:
        report(r["status"].lower(), f"{r['minute']} concurrency matches actual",
               f"actual={r['actual']}, computed={r['computed']}")

    print_summary()
    if meta["worst"]:
        pct, m_str, actual, comp = meta["worst"]
        print(f"worst single-minute divergence: {m_str}  actual={actual} computed={comp}  ({pct*100:+.1f}%)")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
