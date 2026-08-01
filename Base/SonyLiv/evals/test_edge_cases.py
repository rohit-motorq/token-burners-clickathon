#!/usr/bin/env python3
"""
Tests for the 25 edge cases + the #0 critical bug catalogued in
Docs/EDGE_CASES.md. Each test operationalizes one doc claim into a live
SQL/Python check against the pipeline's serving tables (names come from
table_names.py — change them there, not here) and/or the independent
Python reference (reference_intervals.csv / golden_ranges.json, built by
reference_intervals.py / golden_ranges.py per README.md step 4).

SKIPs (not fails) if a required table doesn't exist/isn't ingesting yet, or
if a required reference/golden file hasn't been generated — same convention
as every other file in this suite.

Coverage matrix (EDGE_CASES.md "Summary of All Edge Cases" table):

  #  Case                                          Test here / elsewhere
  0  Intra-minute flapping (THE critical bug)       test_ec0_flapping_no_overcount
  1  active->active dup transitions (resume no-op)  test_ledger.py's per-session active-run
                                                      COUNT vs reference (exact match, 200
                                                      sessions) is strictly stronger evidence
                                                      for this than any aggregate count we can
                                                      build here — not duplicated in this file
  2  inactive->inactive dup transitions              same as #1 — test_ledger.py
  3  Events after SessionEnd                         test_ec3_10_23_terminal_absorbs_late_events
  4  AppForegrounded alone doesn't activate           test_ec4_appforegrounded_alone_no_activation
  5  Sparse delta table needs dense read              test_ec5_gap_minute_concurrency_correct
  6  Heartbeats fire while backgrounded               test_ec6_si_phantom_audience_exists
  7  Pause hidden inside VideoHeartbeat               parsing already exercised by every test
                                                      that filters on (event_type, event); see
                                                      data_expectations.py "resume heartbeats"
  8  Peak not additive across dimensions              test_range_queries.py sum-of-peaks trap
  9  Average has 4.7x spread by definition            design choice, not a bug — LLD §12.2
                                                      declares "mean over occupied minutes";
                                                      nothing to assert beyond peak/avg golden
                                                      match already covered elsewhere
  10 VideoError terminality                           test_ec10_videoerror_sets_ended +
                                                      test_ec3_10_23_terminal_absorbs_late_events
                                                      (confirmed terminal/absorbing by the team —
                                                      see reconciliation note below)
  11 audio_language drift                             not a session_state dimension (LLD pins
                                                      platform/country/video_type/category
                                                      only) — N/A to the serving-layer design
  12 subtitle_language drift                          same as #11 — N/A
  13 Shared sessions (2 user_ids)                      test_ec11_13_25_dimension_pinning
                                                      (user_id pinning folded into same check)
  14 301-session bot user                              informational only, no correctness
                                                      impact per doc — not tested
  15 Mismatched BG/FG counts                           test_ec15_mismatched_bgfg_closes_via_timeout
  16 Duplicate raw events                              data_expectations.py (evidence) +
                                                      test_ledger.py (per-session count match)
  17 Foreground default assumed pre-Play               fg=1 is a column DEFAULT (test_schema.py
                                                      doesn't check defaults); not independently
                                                      isolable without per-event replay — skip
  18 Sessions crossing day boundary                    test_ec18_day_boundary_single_partition
  19 43-hour marathon session                          test_ec19_marathon_session_capped
  20 Zero/near-zero-duration sessions                  test_ec20_near_zero_duration_sessions_short_runs
  21 Duplicate Start/Play/End                           test_ledger.py per-session run count
                                                      vs reference already exercises this
  22 Out-of-order events (0 true OOO)                  data_expectations.py "events never out
                                                      of order"
  23 Late arrivals after SessionEnd                    test_ec3_23_terminal_absorbs_late_events
  24 Multi-platform users                              informational only — not tested
  25 content_id switch mid-session                     test_ec11_13_25_dimension_pinning

Reconciliation note — #10, VideoError terminality:
Neither design doc matches the actual pipeline behavior. EDGE_CASES.md's state
machine (§3.1) has VideoError go ACTIVE -> INACTIVE, explicitly "NOT terminal —
55 sessions continue." LLD-sam.md's §3.1 event catalog gives VideoError NO
switch effect at all (dash for fg/playing/ended). Confirmed with the team:
the actual rule is a third position — VideoError IS terminal/absorbing, same
as VideoSessionEnd (sets ended=1, session closed for good). Tested as such
below. 293 sessions in ch_hackathon_raw_data raise VideoError; all 293
eventually also carry a VideoSessionEnd (consistent with absorbing: a later
duplicate End is a no-op, same as EC3's "multiple VideoSessionEnd" case).
LLD-sam.md and EDGE_CASES.md should both be updated to match this decision —
flagging for the team, not silently patching the design docs here.
"""
import datetime
import json
import sys

from ch_client import query, scalar, table_ready
from table_names import (
    RAW_EVENTS, CONTENT_DIM, SESSION_STATE, SESSION_RUNS,
    CC_DELTA_DIMS, CC_SI_MINUTE, EVENTS_RAW_IMPL,
)

RESULTS = {"pass": 0, "fail": 0, "skip": 0}
UTC = datetime.timezone.utc


def report(status, name, detail=""):
    RESULTS[status] += 1
    tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n        -> {detail}"
    print(line)


def load_golden(range_name, filter_name):
    try:
        with open("golden_ranges.json") as f:
            golden = json.load(f)
    except FileNotFoundError:
        return None
    return next((g for g in golden
                 if g["range_name"] == range_name and g["filter_name"] == filter_name), None)


# ---------------------------------------------------------------------------
# #0 — the critical bug: intra-minute flapping overcounts peak concurrency
# ---------------------------------------------------------------------------

def test_ec0_flapping_no_overcount():
    name = "EC0: minute-grain running-sum peak avoids the naive-delta flapping overcount"
    missing = [] if table_ready(CC_DELTA_DIMS) else [CC_DELTA_DIMS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    golden = load_golden("full_event_day", "no_filter")
    if golden is None:
        report("skip", name, "golden_ranges.json missing full_event_day/no_filter — run golden_ranges.py")
        return

    # The exact bug the doc warns about: "every resume -> +1, every pause -> -1"
    # with no prev-state check. This is a negative control, not the design under
    # test — it should overcount, proving the assertion below is meaningful.
    naive_peak = scalar(f"""
        SELECT max(cc) FROM (
            SELECT sum(d) OVER (ORDER BY minute) AS cc
            FROM (
                SELECT toStartOfMinute(fromUnixTimestamp64Milli(toInt64(event_timestamp))) AS minute,
                       sum(multiIf(event_type = 'VideoHeartbeat' AND event = 'resume', 1,
                                   event_type = 'VideoHeartbeat' AND event = 'pause', -1, 0)) AS d
                FROM {RAW_EVENTS}
                GROUP BY minute
            )
        )
    """)
    impl_peak = scalar(f"""
        SELECT max(cc) FROM (
            SELECT sum(d) OVER (ORDER BY minute) AS cc
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_DIMS}
                  WHERE toDate(minute) = '2026-07-26' GROUP BY minute)
        )
    """)
    golden_peak = golden["peak_concurrency"]

    if naive_peak is None or impl_peak is None:
        report("fail", name, f"naive_peak={naive_peak}, impl_peak={impl_peak} — empty result")
        return
    if naive_peak <= golden_peak:
        report("skip", name, f"naive control ({naive_peak}) didn't overcount vs golden ({golden_peak}) "
                              "— negative control not exercised on this data")
        return
    impl_ok = abs(impl_peak - golden_peak) <= max(3, 0.03 * golden_peak)
    if impl_ok and impl_peak < naive_peak:
        report("pass", name, f"impl={impl_peak} (golden {golden_peak}), naive-flapping control={naive_peak}")
    else:
        report("fail", name, f"impl={impl_peak} vs golden {golden_peak} (tol 3%); naive control={naive_peak}")


# ---------------------------------------------------------------------------
# #3 / #10 / #23 — terminal state absorbs everything, including late arrivals.
# VideoError is terminal here too (confirmed against the actual pipeline
# design, which diverges from both LLD-sam.md's DDL table — no switch effect
# at all — and EDGE_CASES.md — "not terminal, deactivates only"): the first
# VideoSessionEnd OR VideoError, whichever comes first, closes the session
# for good.
# ---------------------------------------------------------------------------

def test_ec3_10_23_terminal_absorbs_late_events():
    name = "EC3/EC10/EC23: no session_runs row starts or extends after a session's first VideoSessionEnd/VideoError"
    missing = [] if table_ready(SESSION_RUNS) else [SESSION_RUNS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    # session_runs.run_start/run_end are DateTime('UTC') per LLD §2.7 — second
    # precision, NOT DateTime64. Compare in seconds, not toUnixTimestamp64Milli
    # (which requires DateTime64 and would error against this column type).
    n = scalar(f"""
        WITH terminals AS (
            SELECT video_session_id AS sid, min(event_timestamp) AS term_ts
            FROM {RAW_EVENTS} WHERE event_type IN ('VideoSessionEnd', 'VideoError')
            GROUP BY sid
        )
        SELECT count() FROM {SESSION_RUNS} r
        JOIN terminals t ON r.video_session_id = t.sid
        WHERE toUnixTimestamp(r.run_start) > toInt64(t.term_ts / 1000) + 1
           OR toUnixTimestamp(r.run_end)   > toInt64(t.term_ts / 1000) + 1
    """)
    if n:
        report("fail", name, f"{n} session_runs rows extend past the first terminal event (+1s slack)")
    else:
        report("pass", name)


def test_ec10_videoerror_sets_ended():
    name = "EC10: every session that ever raises VideoError ends up with ended=1 (VideoError is terminal)"
    missing = [] if table_ready(SESSION_STATE) else [SESSION_STATE]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id, argMax(ended, ver) AS ended
            FROM {SESSION_STATE} GROUP BY video_session_id
        ) s
        WHERE s.video_session_id IN (
            SELECT video_session_id FROM {RAW_EVENTS} WHERE event_type = 'VideoError'
        )
        AND s.ended != 1
    """)
    if n:
        report("fail", name, f"{n} sessions with a VideoError are not marked ended=1")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #4 — AppForegrounded alone never opens a run
# ---------------------------------------------------------------------------

def test_ec4_appforegrounded_alone_no_activation():
    name = "EC4: AppForegrounded events occurring before a session's first VideoPlay never open a run"
    missing = [] if table_ready(SESSION_RUNS) else [SESSION_RUNS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH fg_before_play AS (
            SELECT r.video_session_id AS sid, r.event_timestamp AS fg_ts
            FROM {RAW_EVENTS} r
            LEFT JOIN (
                SELECT video_session_id AS sid2, min(event_timestamp) AS play_ts
                FROM {RAW_EVENTS} WHERE event_type = 'VideoPlay'
                GROUP BY sid2
            ) p ON r.video_session_id = p.sid2
            WHERE r.event_type = 'AppForegrounded'
              AND (p.play_ts IS NULL OR r.event_timestamp < p.play_ts)
        )
        SELECT count() FROM fg_before_play f
        JOIN {SESSION_RUNS} sr ON sr.video_session_id = f.sid
        WHERE abs(toUnixTimestamp(sr.run_start) - toInt64(f.fg_ts / 1000)) < 2
    """)
    if n:
        report("fail", name, f"{n} runs opened within 1s of a pre-Play AppForegrounded event (Rule 3 violated)")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #5 — sparse delta table: a minute with no delta row must still resolve to
# the correct carried-forward concurrency, not zero
# ---------------------------------------------------------------------------

def test_ec5_gap_minute_concurrency_correct():
    name = "EC5: a minute with no delta row still resolves to the correct (non-zero) running concurrency"
    missing = [] if table_ready(CC_DELTA_DIMS) else [CC_DELTA_DIMS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    try:
        from range_metrics import load_intervals, range_metrics
        from test_range_queries import peak_avg_from_delta_dims
        intervals = load_intervals()
    except FileNotFoundError:
        report("skip", name, "reference_intervals.csv missing — run reference_intervals.py first")
        return

    hour_start = int(datetime.datetime(2026, 7, 26, 10, 0, tzinfo=UTC).timestamp())
    present = {r["m"] for r in query(f"""
        SELECT DISTINCT toUnixTimestamp(minute) AS m FROM {CC_DELTA_DIMS}
        WHERE minute >= toDateTime('2026-07-26 10:00:00') AND minute < toDateTime('2026-07-26 11:00:00')
    """)}
    all_minutes = [hour_start + i * 60 for i in range(60)]
    missing_minutes = [m for m in all_minutes if m not in present]
    if not missing_minutes:
        report("skip", name, "no gap minute found in 10:00-11:00 UTC on this dataset — dense already, nothing to prove")
        return

    m = missing_minutes[0]
    impl_peak, _ = peak_avg_from_delta_dims(m * 1000, (m + 60) * 1000, {})
    golden = range_metrics(intervals, m * 1000, (m + 60) * 1000, {})
    golden_peak = golden["peak_concurrency"]
    if impl_peak is not None and abs(impl_peak - golden_peak) <= max(3, 0.03 * golden_peak):
        report("pass", name, f"gap minute {m}: impl={impl_peak}, golden={golden_peak}")
    else:
        report("fail", name, f"gap minute {m}: impl={impl_peak} vs golden={golden_peak}")


# ---------------------------------------------------------------------------
# #6 — pocket heartbeats inflate SI presence beyond the true foreground set
# ---------------------------------------------------------------------------

def test_ec6_si_phantom_audience_exists():
    name = "EC6: SI distinct-session presence >= SA distinct active sessions (phantom audience from pocket heartbeats)"
    missing = [n for n in (CC_SI_MINUTE, SESSION_RUNS) if not table_ready(n)]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    si_distinct = scalar(f"SELECT uniqCombinedMerge(sessions_state) FROM {CC_SI_MINUTE}")
    sa_distinct = scalar(f"SELECT count(DISTINCT video_session_id) FROM {SESSION_RUNS} WHERE sign = 1")
    if si_distinct is None or sa_distinct is None:
        report("fail", name, "empty result")
        return
    if si_distinct >= sa_distinct:
        report("pass", name, f"SI={si_distinct} >= SA={sa_distinct}")
    else:
        report("fail", name, f"SI={si_distinct} < SA={sa_distinct} — SI should be a superset (presence >= true watching)")


# ---------------------------------------------------------------------------
# #11/#13/#25 — dimensions pinned at session start, never drift mid-session
# ---------------------------------------------------------------------------

def test_ec11_13_25_dimension_pinning():
    name = "EC11/13/25: platform/content_id pinned to first-event value for sessions with raw mid-session drift"
    missing = [] if table_ready(SESSION_STATE) else [SESSION_STATE]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id AS sid,
                   argMax(platform, ver) AS pinned_platform,
                   argMax(content_id, ver) AS pinned_content
            FROM {SESSION_STATE} GROUP BY video_session_id
        ) s
        INNER JOIN (
            SELECT video_session_id AS sid, argMin(platform, event_timestamp) AS first_platform,
                   argMin(content_id, event_timestamp) AS first_content
            FROM {RAW_EVENTS} GROUP BY sid
            HAVING count(DISTINCT platform) > 1 OR count(DISTINCT content_id) > 1
        ) f USING sid
        WHERE s.pinned_platform != f.first_platform OR s.pinned_content != f.first_content
    """)
    if n:
        report("fail", name, f"{n} drifted sessions have session_state dims != first-event dims (pinning broken)")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #15 — mismatched BG/FG counts still close via the 90s timeout, not left dangling
# ---------------------------------------------------------------------------

def test_ec15_mismatched_bgfg_closes_via_timeout():
    name = "EC15: every session with a VideoSessionEnd has no dangling open_run_start left in session_state"
    missing = [] if table_ready(SESSION_STATE) else [SESSION_STATE]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH ended AS (
            SELECT video_session_id AS sid FROM {RAW_EVENTS}
            WHERE event_type = 'VideoSessionEnd' GROUP BY sid
        )
        SELECT count() FROM (
            SELECT video_session_id AS sid, argMax(open_run_start, ver) AS ors
            FROM {SESSION_STATE} GROUP BY video_session_id
        ) s
        JOIN ended e USING sid
        WHERE s.ors IS NOT NULL
    """)
    if n:
        report("fail", name, f"{n} ended sessions still have an open run — mismatched BG/FG not closed")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #18 — sessions crossing a UTC day boundary stay in one partition
# ---------------------------------------------------------------------------

def test_ec18_day_boundary_single_partition():
    name = "EC18: sessions whose events span >1 calendar date land in a single events_raw partition"
    missing = [] if table_ready(EVENTS_RAW_IMPL) else [EVENTS_RAW_IMPL]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH cross_day AS (
            SELECT video_session_id AS sid FROM {RAW_EVENTS}
            GROUP BY sid
            HAVING count(DISTINCT toDate(fromUnixTimestamp64Milli(toInt64(event_timestamp)))) > 1
        )
        SELECT count() FROM (
            SELECT video_session_id, count(DISTINCT _partition_id) AS n_parts
            FROM {EVENTS_RAW_IMPL}
            WHERE video_session_id IN (SELECT sid FROM cross_day)
            GROUP BY video_session_id
        ) WHERE n_parts > 1
    """)
    if n:
        report("fail", name, f"{n} day-crossing sessions span >1 partition (PARTITION BY session_start not holding)")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #19 — the 43-hour marathon session gets split into bounded runs, not one giant run
# ---------------------------------------------------------------------------

def test_ec19_marathon_session_capped():
    name = "EC19: the longest-span raw session does not produce one run covering its entire span"
    missing = [] if table_ready(SESSION_RUNS) else [SESSION_RUNS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    row = query(f"""
        SELECT video_session_id AS sid, max(event_timestamp) - min(event_timestamp) AS span
        FROM {RAW_EVENTS} GROUP BY sid ORDER BY span DESC LIMIT 1
    """)
    if not row:
        report("skip", name, "raw table empty")
        return
    top_sid, total_span_ms = row[0]["sid"], int(row[0]["span"])
    max_run_ms = scalar(f"""
        SELECT max(toUnixTimestamp(run_end) - toUnixTimestamp(run_start)) * 1000
        FROM {SESSION_RUNS} WHERE video_session_id = '{top_sid}'
    """)
    if max_run_ms is None:
        report("pass", name, f"session {top_sid} (span={total_span_ms}ms) produced no single-run coverage")
        return
    if max_run_ms < total_span_ms * 0.9:
        report("pass", name, f"session {top_sid}: longest run={max_run_ms}ms << total span={total_span_ms}ms")
    else:
        report("fail", name, f"session {top_sid}: a single run ({max_run_ms}ms) covers ~all of its "
                              f"{total_span_ms}ms span — 90s timeout not splitting the marathon")


# ---------------------------------------------------------------------------
# #20 — zero/near-zero-duration sessions net out to ~0 active time
#
# Live-verified: "no VideoPlay ever" matches 0 sessions in this dataset (every
# session eventually plays) — that definition would be vacuous. The real
# population is sessions whose VideoSessionEnd lands within 2s of VideoPlay
# (44 sessions, live-checked). Per LLD's state machine these DO get a run —
# Play opens it (R+), End immediately closes it (R-hard) — so "zero rows" is
# the wrong invariant too. The correct claim from EDGE_CASES.md ("net 0 delta,
# natural") is that no such session produces a long-lived run.
# ---------------------------------------------------------------------------

def test_ec20_near_zero_duration_sessions_short_runs():
    name = "EC20: sessions with Play->End under 2s never produce a run longer than 5s"
    missing = [] if table_ready(SESSION_RUNS) else [SESSION_RUNS]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH near_zero AS (
            SELECT video_session_id FROM {RAW_EVENTS}
            GROUP BY video_session_id
            HAVING countIf(event_type = 'VideoPlay') > 0
               AND countIf(event_type = 'VideoSessionEnd') > 0
               AND maxIf(event_timestamp, event_type = 'VideoSessionEnd')
                   - minIf(event_timestamp, event_type = 'VideoPlay') < 2000
        )
        SELECT count() FROM {SESSION_RUNS} r
        WHERE r.sign = 1
          AND r.video_session_id IN (SELECT video_session_id FROM near_zero)
          AND toUnixTimestamp(r.run_end) - toUnixTimestamp(r.run_start) > 5
    """)
    if n:
        report("fail", name, f"{n} near-zero-duration sessions produced a run longer than 5s")
    else:
        report("pass", name)


def main():
    test_ec0_flapping_no_overcount()
    test_ec3_10_23_terminal_absorbs_late_events()
    test_ec10_videoerror_sets_ended()
    test_ec4_appforegrounded_alone_no_activation()
    test_ec5_gap_minute_concurrency_correct()
    test_ec6_si_phantom_audience_exists()
    test_ec11_13_25_dimension_pinning()
    test_ec15_mismatched_bgfg_closes_via_timeout()
    test_ec18_day_boundary_single_partition()
    test_ec19_marathon_session_capped()
    test_ec20_near_zero_duration_sessions_short_runs()

    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed, {RESULTS['skip']} skipped")
    sys.exit(1 if RESULTS["fail"] else 0)


if __name__ == "__main__":
    main()
