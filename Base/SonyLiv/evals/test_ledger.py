#!/usr/bin/env python3
"""
Tests for session_runs (LLD §2.7) — the audit ledger that makes retraction
(§10) possible. Every emitted (+1,-1) run gets appended here; this is the
only table with both session identity AND active-interval semantics, so it's
the one place we can cross-check against reference_intervals.csv per-session
instead of just in aggregate.

Checks:
  1. sign only ever 1 or -1
  2. run_start < run_end for every row (no zero/negative-duration or backwards runs)
  3. no two active (sign=1) runs for the same session overlap in time — a
     session can only be "in one run" at a time, per the open_run_start gate
  4. per-session run COUNT (sign=1) matches the reference's active-interval
     count for a sample of sessions — this is the strongest available check
     that the fold's OFF->ON transition counting matches the independent
     ground truth, since aggregate delta tables discard session identity

All SKIP if session_runs doesn't exist yet.
"""
import csv
import sys
from collections import defaultdict

from ch_client import query, scalar, table_exists, table_ready
from table_names import SESSION_RUNS

RESULTS = {"pass": 0, "fail": 0, "skip": 0}


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def load_reference_run_counts(path="reference_intervals.csv"):
    counts = defaultdict(int)
    with open(path) as f:
        for row in csv.DictReader(f):
            counts[row["session_id"]] += 1
    return counts


def main():
    if not table_ready(SESSION_RUNS):
        for name in (
            "sign column only ever 1 or -1",
            "run_start < run_end for every row",
            "no overlapping sign=1 runs for the same session",
            "per-session active-run count matches reference (sample)",
        ):
            report("skip", name, f"{SESSION_RUNS} does not exist yet")
        print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
        sys.exit(0)

    name = "sign column only ever 1 or -1"
    n = scalar(f"SELECT count() FROM {SESSION_RUNS} WHERE sign NOT IN (1, -1)")
    report("fail" if n else "pass", name, f"{n} rows with invalid sign" if n else "")

    name = "run_start < run_end for every row"
    n = scalar(f"SELECT count() FROM {SESSION_RUNS} WHERE run_start >= run_end")
    report("fail" if n else "pass", name, f"{n} rows with run_start >= run_end" if n else "")

    name = "no overlapping sign=1 runs for the same session"
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id, run_start,
                   lagInFrame(run_end) OVER (PARTITION BY video_session_id ORDER BY run_start) AS prev_end
            FROM {SESSION_RUNS}
            WHERE sign = 1
        ) WHERE prev_end IS NOT NULL AND run_start < prev_end
    """)
    report("fail" if n else "pass", name, f"{n} overlapping run pairs — same session counted twice concurrently" if n else "")

    name = "per-session active-run count matches reference (sample of 200 sessions)"
    ref_counts = load_reference_run_counts()
    sample_sessions = list(ref_counts.keys())[:200]
    if not sample_sessions:
        report("skip", name, "reference_intervals.csv empty — run reference_intervals.py first")
    else:
        in_clause = ",".join(f"'{s}'" for s in sample_sessions)
        impl_rows = query(f"""
            SELECT video_session_id, count() AS n
            FROM {SESSION_RUNS}
            WHERE sign = 1 AND video_session_id IN ({in_clause})
            GROUP BY video_session_id
        """)
        impl_counts = {r["video_session_id"]: int(r["n"]) for r in impl_rows}
        mismatches = []
        for sid in sample_sessions:
            impl_n = impl_counts.get(sid, 0)
            ref_n = ref_counts[sid]
            if impl_n != ref_n:
                mismatches.append((sid, impl_n, ref_n))
        if mismatches:
            report("fail", name, f"{len(mismatches)}/{len(sample_sessions)} sessions diverge; "
                                  f"first: {mismatches[:5]} (impl, golden)")
        else:
            report("pass", name, f"{len(sample_sessions)}/{len(sample_sessions)} sessions match exactly")

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
