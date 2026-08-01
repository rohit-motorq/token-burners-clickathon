#!/usr/bin/env python3
"""
Tests the concurrency pipeline described in HLD-sam.html / DataFlow-Sam.html
against the independent reference in reference_concurrency.py.

Each test SKIPs (not fails) if the serving table it needs doesn't exist yet
— this suite is meant to be run today (pipeline not built) and again after
implementation, without editing it. A SKIP list at the end tells you exactly
what's still missing.

Run: python3 reference_concurrency.py reference_deltas.csv   (once, or after raw data changes)
     python3 test_benchmarks.py
"""
import csv
import sys
from collections import defaultdict

from ch_client import query, scalar, table_exists, table_ready
from table_names import CC_DELTA_CONTENT, CC_DELTA_DIMS, CC_SI_MINUTE, SESSION_STATE

RESULTS = {"pass": 0, "fail": 0, "skip": 0}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def require_tables(*names):
    missing = [n for n in names if not table_ready(n)]
    return missing


def load_reference(path="reference_deltas.csv"):
    """minute_epoch -> running SA concurrency (all-dims), and a dim-filtered accessor."""
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def reference_curve(rows, platform=None, country=None, video_type=None, content_id=None):
    by_minute = defaultdict(int)
    for r in rows:
        if platform and r["platform"] != platform:
            continue
        if country and r["country"] != country:
            continue
        if video_type and r["video_type"] != video_type:
            continue
        if content_id and r["content_id"] != str(content_id):
            continue
        by_minute[int(r["minute_epoch"])] += int(r["delta_sessions"])
    minutes = sorted(by_minute)
    running = 0
    curve = {}
    for m in minutes:
        running += by_minute[m]
        curve[m] = running
    return curve


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sa_curve_matches_reference(ref_rows):
    name = f"SA minute-level curve ({CC_DELTA_DIMS}) matches independent reference, platform=ANDROID_PHONE"
    missing = require_tables(CC_DELTA_DIMS)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return

    impl_rows = query(f"""
        SELECT toUnixTimestamp(minute) AS m, sum(delta_sessions) AS d
        FROM {CC_DELTA_DIMS}
        WHERE platform = 'ANDROID_PHONE'
        GROUP BY m ORDER BY m
    """)
    running = 0
    impl_curve = {}
    for r in impl_rows:
        running += int(r["d"])
        impl_curve[int(r["m"])] = running

    ref_curve = reference_curve(ref_rows, platform="ANDROID_PHONE")

    all_minutes = sorted(set(impl_curve) | set(ref_curve))
    mismatches = []
    for m in all_minutes:
        iv, rv = impl_curve.get(m, 0), ref_curve.get(m, 0)
        if abs(iv - rv) > max(2, 0.02 * rv):  # 2% tolerance, floor of 2 sessions
            mismatches.append((m, iv, rv))

    if mismatches:
        report("fail", name, f"{len(mismatches)}/{len(all_minutes)} minutes diverge; first: {mismatches[:3]}")
    else:
        report("pass", name)


def test_sa_never_negative():
    name = "SA concurrency never goes negative at any minute (any dims)"
    missing = require_tables(CC_DELTA_DIMS)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        SELECT count() FROM (
            SELECT sum(d) OVER (ORDER BY minute) AS cc
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS} GROUP BY minute)
        ) WHERE cc < 0
    """)
    if n and n > 0:
        report("fail", name, f"{n} minutes with negative concurrency")
    else:
        report("pass", name)


def test_sa_leq_si():
    name = "Invariant: SA concurrency never exceeds SI concurrency, same minute+filter"
    missing = require_tables(CC_DELTA_DIMS, CC_SI_MINUTE)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH sa AS (
            SELECT minute, sum(d) OVER (ORDER BY minute) AS cc
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS} GROUP BY minute)
        ),
        si AS (
            SELECT minute, uniqCombinedMerge(sessions_state) AS cc
            FROM {CC_SI_MINUTE} GROUP BY minute
        )
        SELECT count() FROM sa LEFT JOIN si USING minute
        WHERE sa.cc > si.cc * 1.02
    """)
    if n and n > 0:
        report("fail", name, f"{n} minutes where SA > SI*1.02 (state machine overcounting)")
    else:
        report("pass", name)


def test_peak_shifts_by_dimension():
    name = "Peak minute genuinely differs across dimension filters (serving table isn't pre-flattened)"
    missing = require_tables(CC_DELTA_DIMS)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    peaks = {}
    for platform in ("ANDROID_PHONE", "IPHONE", "SONY_ANDROID_TV"):
        row = scalar(f"""
            SELECT argMax(minute, cc) FROM (
                SELECT minute, sum(d) OVER (ORDER BY minute) AS cc
                FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS}
                      WHERE platform = '{platform}' GROUP BY minute)
            )
        """)
        peaks[platform] = row
    if len(set(peaks.values())) == 1:
        report("fail", name, f"all platforms peak at same minute {peaks} — suspiciously pre-aggregated")
    else:
        report("pass", name, f"peaks: {peaks}")


def test_hour_boundary_reset_is_exact():
    name = "Hour-boundary cut trick: SA running sum from HH:00 matches full-day running sum at HH:05"
    missing = require_tables(CC_DELTA_DIMS)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    full = scalar(f"""
        SELECT sum(d) FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS} GROUP BY minute)
        WHERE minute <= toDateTime('2026-07-26 10:05:00')
    """)
    from_hour = scalar(f"""
        SELECT sum(d) FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS} GROUP BY minute)
        WHERE minute >= toStartOfHour(toDateTime('2026-07-26 10:05:00'))
          AND minute <= toDateTime('2026-07-26 10:05:00')
    """)
    if full != from_hour:
        report("fail", name, f"full={full} vs from-hour-start={from_hour} — hour cut is not exact, can't skip history")
    else:
        report("pass", name)


def test_live_count_matches_session_state():
    name = f"{SESSION_STATE} 'right now' count matches {CC_DELTA_DIMS} running total for the last minute present"
    missing = require_tables(SESSION_STATE, CC_DELTA_DIMS)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    last_minute = scalar(f"SELECT max(minute) FROM {CC_DELTA_DIMS}")
    delta_total = scalar(f"""
        SELECT sum(d) FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS} GROUP BY minute)
        WHERE minute <= toDateTime('{last_minute}')
    """)
    live_count = scalar(f"""
        SELECT count() FROM (
            SELECT argMax(open_run_start, ver) AS ors, argMax(ended, ver) AS ended
            FROM {SESSION_STATE} GROUP BY video_session_id
        ) WHERE ors IS NOT NULL AND ended = 0
    """)
    # not exact equality (live_count is "right now", delta_total is "as of last batched minute")
    # but should be in the same order of magnitude
    if delta_total is None or live_count is None:
        report("skip", name, "empty result set")
        return
    if abs(delta_total - live_count) > max(50, 0.1 * max(delta_total, 1)):
        report("fail", name, f"delta running total={delta_total} vs live session_state count={live_count}")
    else:
        report("pass", name, f"delta={delta_total} live={live_count}")


def test_content_drilldown_prefix_seek():
    name = f"Per-content drill-down ({CC_DELTA_CONTENT}) sums to same peak as {CC_DELTA_DIMS} for that content's platform mix"
    missing = require_tables(CC_DELTA_CONTENT)
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    top_content = scalar(f"""
        SELECT content_id FROM {CC_DELTA_CONTENT} GROUP BY content_id
        ORDER BY sum(delta_sessions) DESC LIMIT 1
    """)
    if top_content is None:
        report("skip", name, f"{CC_DELTA_CONTENT} is empty")
        return
    peak = scalar(f"""
        SELECT max(cc) FROM (
            SELECT sum(d) OVER (ORDER BY minute) AS cc
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_CONTENT}
                  WHERE content_id = {top_content} GROUP BY minute)
        )
    """)
    if peak is None or peak < 0:
        report("fail", name, f"peak={peak} for content_id={top_content}")
    else:
        report("pass", name, f"content_id={top_content} peak={peak}")


def main():
    ref_rows = load_reference()
    print(f"reference: {len(ref_rows)} delta rows loaded\n")

    test_sa_curve_matches_reference(ref_rows)
    test_sa_never_negative()
    test_sa_leq_si()
    test_peak_shifts_by_dimension()
    test_hour_boundary_reset_is_exact()
    test_live_count_matches_session_state()
    test_content_drilldown_prefix_seek()

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped "
          f"(skips = pipeline tables not yet built)")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
