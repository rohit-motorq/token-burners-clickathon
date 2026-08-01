#!/usr/bin/env python3
"""
Time-range benchmark tests — the shape of query the judges actually run:
"active user/session count for [start, end), filtered by dimension X."

Golden values come from golden_ranges.py (independent Python fold over raw
events, see reference_intervals.py / range_metrics.py) — concrete ranges
picked out of the real event-day timeline: quiet pre-event, ramp-up,
sustained peak, a single peak minute, ramp-down, the full event hour, the
full day.

Two kinds of check per range x filter combo:

1. peak_concurrency / avg_concurrency — answerable EXACTLY from cc_delta_dims
   (running sum of deltas, restricted to the window). Compared to golden
   with a tight tolerance.

2. distinct_active_sessions / distinct_active_users — NOT answerable exactly
   from any table in the current design: cc_delta_dims/cc_delta_content only
   keep aggregate counts, they throw away session/user identity by design
   (that's what makes them cheap at scale). The only thing with identity-
   preserving sketches is cc_si_minute (session_state/user sketches), but
   that table counts NAIVE presence (includes backgrounded/paused time,
   ~9% over per the EDA). So this suite checks it as an upper bound only:
   foreground-only distinct count (golden) <= SI's merged sketch count.
   If the pipeline needs an EXACT distinct-user-in-range answer, that's a
   gap against this design — worth flagging to the team, not silently
   patched here.

Every check SKIPs if its table doesn't exist yet.
"""
import json
import sys

from ch_client import query, scalar, table_exists, table_ready
from table_names import CC_DELTA_DIMS, CC_SI_MINUTE

RESULTS = {"pass": 0, "fail": 0, "skip": 0}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def sql_filter_clause(filters):
    parts = []
    for k, v in filters.items():
        if isinstance(v, (list, set, tuple)):
            vals = ", ".join(f"'{x.replace(chr(39), chr(39)*2)}'" for x in v)
            parts.append(f"{k} IN ({vals})")
        else:
            v_escaped = v.replace("'", "''")
            parts.append(f"{k} = '{v_escaped}'")
    return (" AND " + " AND ".join(parts)) if parts else ""


def peak_avg_from_delta_dims(start_ms, end_ms, filters):
    """Exact peak + time-weighted avg concurrency in [start_ms, end_ms) from
    cc_delta_dims. Running sum is seeded from the start of the day so it's
    correct regardless of hour-boundary alignment; only the query's semantic
    correctness is under test here, not the hour-boundary latency optimization
    (that's covered separately in test_benchmarks.py)."""
    start_dt = f"fromUnixTimestamp64Milli({start_ms})"
    end_dt = f"fromUnixTimestamp64Milli({end_ms})"
    fclause = sql_filter_clause(filters)
    sql = f"""
        WITH stepped AS (
            SELECT
                minute,
                sum(d) OVER (ORDER BY minute) AS cc,
                leadInFrame(minute, 1, {end_dt})
                    OVER (ORDER BY minute ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) AS nxt
            FROM (
                SELECT minute, sum(delta_sessions) AS d
                FROM {CC_DELTA_DIMS}
                WHERE toDate(minute) = toDate({start_dt}) {fclause}
                GROUP BY minute
            )
        )
        SELECT
            maxIf(cc, minute >= {start_dt} AND minute < {end_dt}) AS peak,
            sum(if(nxt > minute AND minute < {end_dt} AND greatest(minute, {start_dt}) < least(nxt, {end_dt}),
                    cc * (dateDiff('millisecond', greatest(minute, {start_dt}), least(nxt, {end_dt}))), 0))
              / ({end_ms} - {start_ms}) AS avg_cc
        FROM stepped
    """
    rows = query(sql)
    if not rows:
        return None, None
    return rows[0].get("peak"), rows[0].get("avg_cc")


def si_merged_count(field, start_ms, end_ms, filters):
    start_dt = f"fromUnixTimestamp64Milli({start_ms})"
    end_dt = f"fromUnixTimestamp64Milli({end_ms})"
    fclause = sql_filter_clause(filters)
    sql = f"""
        SELECT uniqCombinedMerge({field}) AS n
        FROM {CC_SI_MINUTE}
        WHERE minute >= {start_dt} AND minute < {end_dt} {fclause}
    """
    return scalar(sql)


def within_tol(actual, expected, rel=0.03, floor=3):
    if actual is None or expected is None:
        return False
    return abs(actual - expected) <= max(floor, rel * max(expected, 1))


def run_peak_avg_tests(golden):
    missing = [] if table_ready(CC_DELTA_DIMS) else [CC_DELTA_DIMS]
    for g in golden:
        name = f"[{g['range_name']}/{g['filter_name']}] peak+avg concurrency ({CC_DELTA_DIMS}) matches golden"
        if missing:
            report("skip", name, f"missing tables: {missing}")
            continue
        try:
            peak, avg = peak_avg_from_delta_dims(g["start_ts_ms"], g["end_ts_ms"], g["filters"])
        except Exception as e:
            report("fail", name, f"query error: {e}")
            continue
        peak_ok = within_tol(peak, g["peak_concurrency"])
        avg_ok = within_tol(avg, g["avg_concurrency"])
        if peak_ok and avg_ok:
            report("pass", name, f"peak={peak} (golden {g['peak_concurrency']}), "
                                  f"avg={avg:.2f} (golden {g['avg_concurrency']})" if avg is not None else "")
        else:
            report("fail", name, f"peak={peak} vs golden {g['peak_concurrency']}; "
                                  f"avg={avg} vs golden {g['avg_concurrency']}")


def run_distinct_upper_bound_tests(golden):
    missing = [] if table_ready(CC_SI_MINUTE) else [CC_SI_MINUTE]
    for g in golden:
        for kind, field, golden_key in (
            ("sessions", "sessions_state", "distinct_active_sessions"),
            ("users", "users_state", "distinct_active_users"),
        ):
            name = (f"[{g['range_name']}/{g['filter_name']}] FG distinct {kind} <= "
                    f"SI merged sketch ({CC_SI_MINUTE}) — upper bound")
            if missing:
                report("skip", name, f"missing tables: {missing}")
                continue
            try:
                si_count = si_merged_count(field, g["start_ts_ms"], g["end_ts_ms"], g["filters"])
            except Exception as e:
                report("fail", name, f"query error: {e}")
                continue
            golden_val = g[golden_key]
            if si_count is None:
                report("fail", name, f"{CC_SI_MINUTE} returned no rows for this range/filter")
                continue
            # SI sketches carry ~1-2% HLL error; allow a small slack below golden too
            if si_count >= golden_val * 0.97:
                report("pass", name, f"FG golden={golden_val}, SI merged={si_count}")
            else:
                report("fail", name, f"SI merged ({si_count}) < FG golden ({golden_val}) — "
                                      f"SI should always be >= true foreground count")


def run_peak_of_union_regression_test(golden):
    """Guards the exact trap the problem statement's own example warns about
    (and LLD §12.1 names explicitly): peak(A) + peak(B) != peak(A OR B),
    because A and B's peaks can land at different minutes. A dev who
    "optimizes" by precomputing per-platform peaks and summing them would
    pass every single-filter test in this suite and still be wrong the
    moment a query asks for platform IN (A, B). Uses sustained_peak/
    ANDROID_PHONE+SONY_ANDROID_TV, where the golden data shows a real 22-
    session gap between the two methods — not a hypothetical."""
    from range_metrics import load_intervals, range_metrics

    name = "peak(platform IN (A,B)) != peak(A) + peak(B) — sum-of-peaks trap"
    rng = next(g for g in golden if g["range_name"] == "sustained_peak" and g["filter_name"] == "no_filter")
    start_ms, end_ms = rng["start_ts_ms"], rng["end_ts_ms"]
    intervals = load_intervals()

    peak_a = range_metrics(intervals, start_ms, end_ms, {"platform": "ANDROID_PHONE"})["peak_concurrency"]
    peak_b = range_metrics(intervals, start_ms, end_ms, {"platform": "SONY_ANDROID_TV"})["peak_concurrency"]
    wrong_sum = peak_a + peak_b
    correct_union = range_metrics(intervals, start_ms, end_ms,
                                   {"platform": ["ANDROID_PHONE", "SONY_ANDROID_TV"]})["peak_concurrency"]

    if wrong_sum == correct_union:
        report("skip", name, f"sum-of-peaks ({wrong_sum}) happens to equal union peak ({correct_union}) "
                              "on this range — trap not exercised, pick a different range")
        return

    name2 = "implementation's platform-IN query returns the real union peak, not the sum-of-peaks"
    if not table_ready(CC_DELTA_DIMS):
        report("skip", name2, f"{CC_DELTA_DIMS} not ready")
        return
    impl_peak, _ = peak_avg_from_delta_dims(start_ms, end_ms, {"platform": ["ANDROID_PHONE", "SONY_ANDROID_TV"]})
    if impl_peak == correct_union:
        report("pass", name2, f"impl={impl_peak} matches correct union ({correct_union}), "
                               f"NOT the wrong sum-of-peaks ({wrong_sum})")
    elif impl_peak == wrong_sum:
        report("fail", name2, f"impl={impl_peak} matches the WRONG sum-of-peaks ({wrong_sum}), "
                               f"not the real union peak ({correct_union})")
    else:
        report("fail", name2, f"impl={impl_peak} matches neither correct union ({correct_union}) "
                               f"nor wrong sum ({wrong_sum})")


def main():
    with open("golden_ranges.json") as f:
        golden = json.load(f)
    print(f"loaded {len(golden)} golden range/filter combos\n")

    print("-- peak / avg concurrency --")
    run_peak_avg_tests(golden)
    print("\n-- distinct session/user upper-bound checks --")
    run_distinct_upper_bound_tests(golden)
    print("\n-- sum-of-peaks regression --")
    run_peak_of_union_regression_test(golden)

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
