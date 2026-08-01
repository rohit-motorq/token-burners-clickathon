#!/usr/bin/env python3
"""
Check 1 — different peak-concurrency moments across the whole day: find
where concurrency actually peaked (independent Python fold over the full
1440-minute day, not just the hand-picked golden ranges), then check the
pipeline (cc_delta_content) reports the same concurrency at those exact
minutes.

Picks the top N local peaks at least 15 minutes apart (so we're not just
re-testing the same peak's immediate neighbors), which in practice lands on
a spread of distinct moments through the event: the approach to peak, the
peak itself, and the decline.
"""
import datetime
import sys

from common import (
    EVENT_DAY, load_actual_intervals, actual_per_minute_sweep,
    computed_concurrency_at, within_tol, report, print_summary, RESULTS,
)
from ch_client import table_ready
from table_names import CC_DELTA_CONTENT

TOP_N = 6
MIN_SPACING_MINUTES = 15


def pick_local_peaks(per_minute, top_n, spacing_minutes):
    candidates = sorted(per_minute.items(), key=lambda kv: kv[1], reverse=True)
    picked = []
    for minute_str, cc in candidates:
        m = datetime.datetime.strptime(minute_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        if all(abs((m - pm).total_seconds()) >= spacing_minutes * 60 for pm, _ in picked):
            picked.append((m, cc))
        if len(picked) == top_n:
            break
    return sorted(picked)


def collect():
    """Returns (rows, meta) without printing — reusable by build_report.py.
    rows: list of dicts {minute, actual, computed, diff, diff_pct, status}.
    meta: {global_peak_minute, global_peak_val, skip_reason or None}."""
    if not table_ready(CC_DELTA_CONTENT):
        return [], {"skip_reason": f"{CC_DELTA_CONTENT} not ready"}
    try:
        intervals = load_actual_intervals()
    except FileNotFoundError:
        return [], {"skip_reason": "reference_intervals.csv missing — run ../reference_intervals.py first"}

    per_minute = actual_per_minute_sweep(intervals, EVENT_DAY)
    peaks = pick_local_peaks(per_minute, TOP_N, MIN_SPACING_MINUTES)

    rows = []
    for minute_dt, actual_cc in peaks:
        computed_cc = computed_concurrency_at(minute_dt, EVENT_DAY)
        diff = computed_cc - actual_cc
        diff_pct = (diff / actual_cc * 100) if actual_cc else (0.0 if diff == 0 else float("inf"))
        rows.append({
            "minute": minute_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "actual": actual_cc, "computed": computed_cc,
            "diff": diff, "diff_pct": diff_pct,
            "status": "PASS" if within_tol(actual_cc, computed_cc) else "FAIL",
        })

    global_peak_minute, global_peak_val = max(per_minute.items(), key=lambda kv: kv[1])
    return rows, {"skip_reason": None, "global_peak_minute": global_peak_minute, "global_peak_val": global_peak_val}


def main():
    rows, meta = collect()
    if meta.get("skip_reason"):
        report("skip", "daily peak checks", meta["skip_reason"])
        print_summary()
        sys.exit(0)

    print(f"picked {len(rows)} local peaks, >= {MIN_SPACING_MINUTES}min apart:\n")
    for r in rows:
        m = r["minute"][11:16]
        report(r["status"].lower(), f"peak at {m} UTC matches actual", f"actual={r['actual']}, computed={r['computed']}")

    print(f"\nday-wide actual peak: {meta['global_peak_minute']} UTC, {meta['global_peak_val']} concurrent sessions")

    print_summary()
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
