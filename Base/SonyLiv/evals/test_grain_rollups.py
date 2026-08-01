#!/usr/bin/env python3
"""
Explicit hour-grain and day-grain peak/avg tests. Problem statement names
three grains by name: "peak and average concurrency at minute/hour/day
grain." test_range_queries.py covers minute-grain (arbitrary windows) and
day-grain incidentally (the full_event_day golden fixture is a day window)
but nothing exercised hour-grain as its own bucketed rollup — "give me peak
per hour for the whole day," 24 answers in one shape, not one hand-picked
hour.

Reuses peak_avg_from_delta_dims from test_range_queries.py rather than
inventing new SQL: it's already the correctly-seeded (from day start, so
no hour-boundary edge cases) exact peak/avg-in-a-range primitive. Calling
it once per [hour, hour+1) window IS the hour-grain rollup — this is also a
legitimate real design choice (a dashboard bar chart calls "peak in range"
once per bar), not just a test convenience.

Golden values computed independently via range_metrics.py over
reference_intervals.csv, same as the rest of the suite.
"""
import datetime
import sys

from ch_client import table_ready
from range_metrics import load_intervals, range_metrics
from table_names import CC_DELTA_DIMS
from test_range_queries import peak_avg_from_delta_dims, within_tol

RESULTS = {"pass": 0, "fail": 0, "skip": 0}
UTC = datetime.timezone.utc


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def hour_bounds_ms(day, hour):
    start = datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)
    end = start + datetime.timedelta(hours=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def day_bounds_ms(day):
    start = datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + datetime.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def main():
    day = datetime.date(2026, 7, 26)
    intervals = load_intervals()

    print("-- hour-grain rollup (24 buckets) --")
    if not table_ready(CC_DELTA_DIMS):
        for h in range(24):
            report("skip", f"hour={h:02d} peak/avg matches golden", f"{CC_DELTA_DIMS} not ready")
    else:
        for h in range(24):
            name = f"hour={h:02d} peak/avg matches golden"
            start_ms, end_ms = hour_bounds_ms(day, h)
            golden = range_metrics(intervals, start_ms, end_ms)
            try:
                peak, avg = peak_avg_from_delta_dims(start_ms, end_ms, {})
            except Exception as e:
                report("fail", name, f"query error: {e}")
                continue
            peak_ok = within_tol(peak, golden["peak_concurrency"])
            avg_ok = within_tol(avg, golden["avg_concurrency"])
            if peak_ok and avg_ok:
                report("pass", name, f"peak={peak} (golden {golden['peak_concurrency']}), "
                                      f"avg={avg:.2f} (golden {golden['avg_concurrency']})")
            else:
                report("fail", name, f"peak={peak} vs golden {golden['peak_concurrency']}; "
                                      f"avg={avg} vs golden {golden['avg_concurrency']}")

    print("\n-- day-grain rollup (1 bucket, full event day) --")
    name = "full day 2026-07-26 peak/avg matches golden"
    if not table_ready(CC_DELTA_DIMS):
        report("skip", name, f"{CC_DELTA_DIMS} not ready")
    else:
        start_ms, end_ms = day_bounds_ms(day)
        golden = range_metrics(intervals, start_ms, end_ms)
        peak, avg = peak_avg_from_delta_dims(start_ms, end_ms, {})
        peak_ok = within_tol(peak, golden["peak_concurrency"])
        avg_ok = within_tol(avg, golden["avg_concurrency"])
        if peak_ok and avg_ok:
            report("pass", name, f"peak={peak} (golden {golden['peak_concurrency']}), "
                                  f"avg={avg:.2f} (golden {golden['avg_concurrency']})")
        else:
            report("fail", name, f"peak={peak} vs golden {golden['peak_concurrency']}; "
                                  f"avg={avg} vs golden {golden['avg_concurrency']}")

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
