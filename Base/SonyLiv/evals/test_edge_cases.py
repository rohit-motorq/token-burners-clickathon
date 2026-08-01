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
                                                      — PERMANENT SKIP in v2: session_runs (the
                                                      audit ledger this relied on) is dropped,
                                                      no replacement exists
  4  AppForegrounded alone doesn't activate           test_ec4_appforegrounded_alone_no_activation
                                                      — PERMANENT SKIP in v2: session_runs dropped;
                                                      guaranteed structurally by 008_delta_fold.sql's
                                                      activation_ts CTE instead, not queryable
  5  Sparse delta table needs dense read              test_ec5_gap_minute_concurrency_correct
                                                      (now reads cc_delta_content)
  6  Heartbeats fire while backgrounded               test_ec6_si_phantom_audience_exists
                                                      — PERMANENT SKIP in v2: needs both
                                                      cc_si_minute and session_runs, both dropped
  7  Pause hidden inside VideoHeartbeat               parsing already exercised by every test
                                                      that filters on (event_type, event); see
                                                      data_expectations.py "resume heartbeats"
  8  Peak not additive across dimensions              test_range_queries.py sum-of-peaks trap
  9  Average has 4.7x spread by definition            design choice, not a bug — LLD §12.2
                                                      declares "mean over occupied minutes";
                                                      nothing to assert beyond peak/avg golden
                                                      match already covered elsewhere
  10 VideoError terminality                           test_ec10_videoerror_sets_ended (rewritten
                                                      against session_active — see v2
                                                      reconciliation note below; no longer claims
                                                      VideoError itself is terminal, only that it
                                                      eventually correlates with is_active=0)
  11 audio_language drift                             not a session_active dimension (v2 pins
                                                      platform/country/video_type/category
                                                      only) — N/A to the serving-layer design
  12 subtitle_language drift                          same as #11 — N/A
  13 Shared sessions (2 user_ids)                      test_ec11_13_25_dimension_pinning
                                                      (rewritten against session_active — see
                                                      function docstring for the weaker v2 claim)
  14 301-session bot user                              informational only, no correctness
                                                      impact per doc — not tested
  15 Mismatched BG/FG counts                           test_ec15_mismatched_bgfg_closes_via_timeout
                                                      (rewritten against session_active is_active)
  16 Duplicate raw events                              data_expectations.py (evidence) +
                                                      test_ledger.py (per-session count match)
  17 Foreground default assumed pre-Play               fg=1 is a column DEFAULT (test_schema.py
                                                      doesn't check defaults); not independently
                                                      isolable without per-event replay — skip
  18 Sessions crossing day boundary                    test_ec18_day_boundary_single_partition
  19 43-hour marathon session                          test_ec19_marathon_session_capped
                                                      — PERMANENT SKIP in v2: session_runs dropped,
                                                      no per-run duration data exists at all
  20 Zero/near-zero-duration sessions                  test_ec20_near_zero_duration_sessions_short_runs
                                                      — PERMANENT SKIP in v2: same as #19
  21 Duplicate Start/Play/End                           test_ledger.py per-session run count
                                                      vs reference already exercises this
  22 Out-of-order events (0 true OOO)                  data_expectations.py "events never out
                                                      of order"
  23 Late arrivals after SessionEnd                    test_ec3_10_23_terminal_absorbs_late_events
                                                      — PERMANENT SKIP in v2, same as #3
  24 Multi-platform users                              informational only — not tested
  25 content_id switch mid-session                     test_ec11_13_25_dimension_pinning
                                                      (rewritten against session_active, see #13)

Reconciliation note — #10, VideoError terminality (v2 finding):
Checked directly against src/migrationv2/migrations/008_delta_fold.sql (the
live fold logic): VideoError is NOT specially handled anywhere in the fold
(grepped the migration, no match) — only VideoSessionEnd is a hard-end
trigger. A VideoError-only session (no VideoSessionEnd) would only close via
the 90s staleness sweep, not immediately, and there's no `ended` column
anymore, only `is_active`. So v2 does not make VideoError terminal by design
the way the old LLD-based reconciliation assumed — it happens to look
terminal in practice only because (per data_expectations.py) essentially all
VideoError sessions also carry a real VideoSessionEnd, which is what
actually closes them. test_ec10_videoerror_sets_ended checks that weaker,
actually-true claim (eventually is_active=0), not "VideoError is terminal."
Worth flagging to the team as a design note if VideoError should get
explicit fold handling.
"""
import datetime
import json
import sys

from ch_client import query, scalar, table_ready
from table_names import (
    RAW_EVENTS, CONTENT_DIM, SESSION_ACTIVE,
    CC_DELTA_CONTENT, EVENTS_RAW_IMPL,
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
    # ponytail: RAW_EVENTS wasn't gated here before either — this query needs it
    # unconditionally, so check it alongside CC_DELTA_CONTENT to avoid crashing when
    # the raw seed table isn't loaded in this environment (pre-existing gap, not a
    # v2-migration regression).
    missing = [n for n in (CC_DELTA_CONTENT, RAW_EVENTS) if not table_ready(n)]
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
            FROM (SELECT minute, sum(delta_sessions) AS d FROM {CC_DELTA_CONTENT}
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
    """session_runs (the per-session run_start/run_end audit ledger) is
    dropped in v2 with no replacement — v2's session_active only carries
    current is_active state, not historical run boundaries. "does no run
    start/extend after the terminal event" cannot be independently
    re-verified from session_active's current-state-only design. Permanently
    skipped — flag to the team as a coverage gap, same spirit as this file's
    EC17/EC24 "informational only, not tested" convention."""
    name = "EC3/EC10/EC23: no session_runs row starts or extends after a session's first VideoSessionEnd/VideoError"
    report("skip", name, "session_runs removed in v2 design (no audit ledger table) — permanently skipped")


def test_ec10_videoerror_sets_ended():
    """v2 design note: 008_delta_fold.sql's fold logic has no explicit
    VideoError handling at all (grepped the migration — no match) — only
    VideoSessionEnd is a hard-end trigger. A VideoError-only session (no
    VideoSessionEnd) would only close via the 90s staleness sweep, not
    immediately. There's also no `ended` column anymore, only `is_active`.
    So this test now checks the weaker, actually-true-in-v2 claim: every
    session that raised VideoError eventually shows is_active=0 in
    session_active — which holds in practice because (per
    data_expectations.py) essentially all VideoError sessions also carry a
    real VideoSessionEnd, not because v2 treats VideoError as terminal
    itself."""
    name = "EC10 (v2): every session that ever raises VideoError eventually has is_active=0 in session_active"
    missing = [n for n in (SESSION_ACTIVE, RAW_EVENTS) if not table_ready(n)]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id, argMax(is_active, version) AS is_active
            FROM {SESSION_ACTIVE} GROUP BY video_session_id
        ) s
        WHERE s.video_session_id IN (
            SELECT video_session_id FROM {RAW_EVENTS} WHERE event_type = 'VideoError'
        )
        AND s.is_active != 0
    """)
    if n:
        report("fail", name, f"{n} sessions with a VideoError are still is_active=1")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #4 — AppForegrounded alone never opens a run
# ---------------------------------------------------------------------------

def test_ec4_appforegrounded_alone_no_activation():
    """session_runs (the per-session run audit ledger) is dropped in v2 with
    no replacement, so this can no longer be independently verified via a
    queryable ledger. v2's fold (008_delta_fold.sql, the `activation_ts`
    CTE) structurally guarantees this already: activation only fires on
    `event_type = 'VideoPlay' OR (VideoHeartbeat AND event IN ('play',
    'resume'))` — AppForegrounded is never in that condition. Permanently
    skipped, only assertable by reading the SQL itself — same coverage-gap
    treatment as EC3/EC10/EC23."""
    name = "EC4: AppForegrounded events occurring before a session's first VideoPlay never open a run"
    report("skip", name, "session_runs removed in v2 design (no audit ledger table) — permanently skipped; "
                          "guaranteed structurally by 008_delta_fold.sql's activation_ts CTE instead")


# ---------------------------------------------------------------------------
# #5 — sparse delta table: a minute with no delta row must still resolve to
# the correct carried-forward concurrency, not zero
# ---------------------------------------------------------------------------

def test_ec5_gap_minute_concurrency_correct():
    name = "EC5: a minute with no delta row still resolves to the correct (non-zero) running concurrency"
    missing = [] if table_ready(CC_DELTA_CONTENT) else [CC_DELTA_CONTENT]
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
        SELECT DISTINCT toUnixTimestamp(minute) AS m FROM {CC_DELTA_CONTENT}
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
    """Depends on both cc_si_minute (HLL sketch table) and session_runs
    (audit ledger) — both dropped in v2 with no replacement. Permanently
    skipped, flag to team as a coverage gap."""
    name = "EC6: SI distinct-session presence >= SA distinct active sessions (phantom audience from pocket heartbeats)"
    report("skip", name, "cc_si_minute and session_runs both removed in v2 design — permanently skipped")


# ---------------------------------------------------------------------------
# #11/#13/#25 — dimensions pinned at session start, never drift mid-session
# ---------------------------------------------------------------------------

def test_ec11_13_25_dimension_pinning():
    """v2 caveat: unlike v1's explicit "pin at session start" semantics,
    v2's session_active_sync_mv (006_session_active.sql) just carries
    whatever platform/content_id accompanied each batch's
    any(e.platform)/any(e.content_id) (arbitrary-in-batch, not literally
    first-ever). So this test now checks "the LATEST recorded dims match the
    FIRST-event dims" for sessions with raw mid-session drift — a weaker but
    still meaningful claim (it'll typically hold since dims rarely actually
    change, per data_expectations.py's <=2-sessions-drift finding), not a
    tautology, a real assertion — just note the semantic is different from
    v1's guarantee."""
    name = "EC11/13/25: platform/content_id latest-recorded value matches first-event value for sessions with raw mid-session drift"
    missing = [n for n in (SESSION_ACTIVE, RAW_EVENTS) if not table_ready(n)]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id AS sid,
                   argMax(platform, version) AS pinned_platform,
                   argMax(content_id, version) AS pinned_content
            FROM {SESSION_ACTIVE} GROUP BY video_session_id
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
        report("fail", name, f"{n} drifted sessions have session_active dims != first-event dims")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #15 — mismatched BG/FG counts still close via the 90s timeout, not left dangling
# ---------------------------------------------------------------------------

def test_ec15_mismatched_bgfg_closes_via_timeout():
    """v2 rewrite: there's no open_run_start/ended pair anymore, only
    is_active. Equivalent check: every session that saw a real
    VideoSessionEnd should NOT still show is_active=1 in session_active
    (that would mean the sweep/end never closed it). Note: because the fold
    runs asynchronously on a 20-35s refresh cadence, a handful of very
    recently-ended sessions may not have been swept yet — failures limited
    to a few very-recent sessions are a timing/eventual-consistency
    artifact, not a real bug; a systemic pattern would be."""
    name = "EC15 (v2): every session with a VideoSessionEnd is not still is_active=1 in session_active"
    missing = [n for n in (SESSION_ACTIVE, RAW_EVENTS) if not table_ready(n)]
    if missing:
        report("skip", name, f"missing tables: {missing}")
        return
    n = scalar(f"""
        WITH ended AS (
            SELECT video_session_id AS sid FROM {RAW_EVENTS}
            WHERE event_type = 'VideoSessionEnd' GROUP BY sid
        )
        SELECT count() FROM (
            SELECT video_session_id AS sid, argMax(is_active, version) AS is_active
            FROM {SESSION_ACTIVE} GROUP BY video_session_id
        ) s
        JOIN ended e USING sid
        WHERE s.is_active = 1
    """)
    if n:
        report("fail", name, f"{n} ended sessions still show is_active=1 — check if recent (sweep lag) or systemic")
    else:
        report("pass", name)


# ---------------------------------------------------------------------------
# #18 — sessions crossing a UTC day boundary stay in one partition
# ---------------------------------------------------------------------------

def test_ec18_day_boundary_single_partition():
    name = "EC18: sessions whose events span >1 calendar date land in a single events_raw partition"
    missing = [n for n in (EVENTS_RAW_IMPL, RAW_EVENTS) if not table_ready(n)]
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
    """session_runs (per-run duration ledger) is dropped in v2 with no
    replacement — no per-run duration data exists anywhere in v2 design;
    session_active only has current is_active + last_seen, not historical
    run boundaries. Permanently skipped, flag to team as a coverage gap."""
    name = "EC19: the longest-span raw session does not produce one run covering its entire span"
    report("skip", name, "session_runs removed in v2 design (no per-run duration data) — permanently skipped")


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
    """session_runs (per-run duration ledger) is dropped in v2 with no
    replacement — same coverage gap as EC19, no per-run duration data
    exists anywhere in v2. Permanently skipped, flag to team."""
    name = "EC20: sessions with Play->End under 2s never produce a run longer than 5s"
    report("skip", name, "session_runs removed in v2 design (no per-run duration data) — permanently skipped")


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
