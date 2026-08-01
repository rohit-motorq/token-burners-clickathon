#!/usr/bin/env python3
"""
Minute-by-minute actual-vs-computed concurrency for the event day's busiest
hour. Complements test_range_queries.py (range-level peak/avg only) with the
full per-minute curve — the shape judges and dashboards actually render.

"Computed" replicates the query pattern from Docs/CONCURRENCY_VALIDATION.md,
correctly seeded from the start of the day (not the window start — a running
sum seeded at the window start silently drops whatever concurrency was
already carried in from earlier, see that doc's Finding 1).

"Actual" is the independent ground truth: count of reference_intervals.csv
active intervals covering each minute mark, no SQL, no pipeline reuse.

Reports one PASS/FAIL per minute, then a summary and the single worst-diverging
minute (the kind of single-minute spike that's easy to miss skimming 60 rows).
SKIPs entirely if cc_delta_content isn't ready or reference_intervals.csv is
missing (run reference_intervals.py first — ./run.sh does this for you).
"""
import csv
import datetime
import sys

from ch_client import query, table_ready
from table_names import CC_DELTA_CONTENT

RESULTS = {"pass": 0, "fail": 0, "skip": 0}
UTC = datetime.timezone.utc

DAY = "2026-07-26"
WINDOW_START = datetime.datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
WINDOW_END = datetime.datetime(2026, 7, 26, 11, 0, tzinfo=UTC)


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def within_tol(actual, computed, rel=0.03, floor=3):
    return abs(computed - actual) <= max(floor, rel * max(actual, 1))


def load_actual_per_minute(path="reference_intervals.csv"):
    intervals = []
    with open(path) as f:
        for r in csv.DictReader(f):
            intervals.append((int(r["start_ts_ms"]), int(r["end_ts_ms"])))

    counts = {}
    n_minutes = int((WINDOW_END - WINDOW_START).total_seconds() // 60)
    for i in range(n_minutes):
        m = WINDOW_START + datetime.timedelta(minutes=i)
        m_ms = int(m.timestamp() * 1000)
        counts[m.strftime("%Y-%m-%d %H:%M:%S")] = sum(1 for s, e in intervals if s <= m_ms < e)
    return counts


def load_computed_per_minute():
    day_start = datetime.datetime(2026, 7, 26, tzinfo=UTC)
    day_end = day_start + datetime.timedelta(days=1)
    rows = query(f"""
        WITH stepped AS (
            SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent_viewers
            FROM (
                SELECT minute, sum(delta_sessions) AS d
                FROM {CC_DELTA_CONTENT}
                WHERE toDate(minute) = '{DAY}'
                GROUP BY minute
                ORDER BY minute WITH FILL
                    FROM toDateTime('{day_start.strftime("%Y-%m-%d %H:%M:%S")}')
                    TO   toDateTime('{day_end.strftime("%Y-%m-%d %H:%M:%S")}')
                    STEP INTERVAL 1 MINUTE
            )
        )
        SELECT minute, concurrent_viewers FROM stepped
        WHERE minute >= toDateTime('{WINDOW_START.strftime("%Y-%m-%d %H:%M:%S")}')
          AND minute <  toDateTime('{WINDOW_END.strftime("%Y-%m-%d %H:%M:%S")}')
        ORDER BY minute
    """)
    return {r["minute"]: int(r["concurrent_viewers"]) for r in rows}


def main():
    if not table_ready(CC_DELTA_CONTENT):
        report("skip", f"minute-series {WINDOW_START}-{WINDOW_END}", f"{CC_DELTA_CONTENT} not ready")
        print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
        sys.exit(0)

    try:
        actual = load_actual_per_minute()
    except FileNotFoundError:
        report("skip", f"minute-series {WINDOW_START}-{WINDOW_END}",
                "reference_intervals.csv missing — run reference_intervals.py first")
        print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
        sys.exit(0)

    computed = load_computed_per_minute()

    worst = None  # (abs_pct, minute, act, comp)
    for minute, act in actual.items():
        comp = computed.get(minute, 0)
        name = f"{minute} concurrency matches actual"
        ok = within_tol(act, comp)
        report("pass" if ok else "fail", name, f"actual={act}, computed={comp}")
        if act > 0:
            pct = abs(comp - act) / act
            if worst is None or pct > worst[0]:
                worst = (pct, minute, act, comp)

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    if worst:
        pct, minute, act, comp = worst
        print(f"worst single-minute divergence: {minute}  actual={act} computed={comp}  ({pct*100:+.1f}%)")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
