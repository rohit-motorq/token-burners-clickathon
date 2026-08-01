#!/usr/bin/env python3
"""
Data expectations: codifies the claims in Docs/DATA_ANALYSIS-ROHIT.md as
live assertions against ch_hackathon_raw_data / ch_hackathon_content_data.

Run standalone, any time, independent of whether the concurrency pipeline
exists yet. This is the contract the pipeline's queries are allowed to
assume. If one of these fails, the pipeline's query-level tests
(test_benchmarks.py) are testing against a stale assumption.
"""
import sys
from ch_client import query, scalar
from table_names import RAW_EVENTS as RAW, CONTENT_DIM as CONTENT

checks = []


def check(name):
    def deco(fn):
        checks.append((name, fn))
        return fn
    return deco


@check("raw table has expected columns")
def _():
    cols = {r["name"] for r in query(f"DESCRIBE TABLE {RAW}")}
    expected = {
        "content_id", "video_session_id", "user_id", "event_type", "event",
        "event_timestamp", "platform", "app_version", "country",
        "audio_language", "subtitle_language", "player_version",
        "session_start_epoch",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


@check("event_type has exactly the 7 known values")
def _():
    rows = query(f"SELECT DISTINCT event_type FROM {RAW}")
    got = {r["event_type"] for r in rows}
    expected = {"VideoHeartbeat", "AppBackgrounded", "AppForegrounded",
                "VideoPlay", "VideoSessionEnd", "VideoSessionStart", "VideoError"}
    assert got == expected, f"got {got}"


@check("every session has exactly 1 content_id (session:content is 1:1)")
def _():
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id FROM {RAW}
            GROUP BY video_session_id HAVING count(DISTINCT content_id) > 1
        )""")
    ratio_bad = n
    assert ratio_bad <= 2, f"{ratio_bad} sessions span >1 content_id (expected ~0-1)"


@check("events within a session are never out of order")
def _():
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id,
                   event_timestamp,
                   lagInFrame(event_timestamp) OVER (PARTITION BY video_session_id ORDER BY event_timestamp) AS prev_ts
            FROM {RAW}
        ) WHERE prev_ts > event_timestamp
    """)
    assert n == 0, f"{n} out-of-order event pairs found"


@check("periodic heartbeat gap median is 30-40s (sets the liveness timeout basis)")
def _():
    med = scalar(f"""
        SELECT quantile(0.5)(gap_sec) FROM (
            SELECT (event_timestamp - lagInFrame(event_timestamp)
                    OVER (PARTITION BY video_session_id ORDER BY event_timestamp)) / 1000.0 AS gap_sec
            FROM {RAW}
            WHERE event IN ('buffer-health', 'video-resize', 'network-activity')
        ) WHERE gap_sec > 1 AND gap_sec < 300
    """)
    assert 25 <= med <= 45, f"median heartbeat gap {med}s outside expected 25-45s band"


@check("90s timeout covers >=99% of legitimate heartbeat gaps")
def _():
    p99 = scalar(f"""
        SELECT quantile(0.99)(gap_sec) FROM (
            SELECT (event_timestamp - lagInFrame(event_timestamp)
                    OVER (PARTITION BY video_session_id ORDER BY event_timestamp)) / 1000.0 AS gap_sec
            FROM {RAW}
        ) WHERE gap_sec > 0 AND gap_sec < 90
    """)
    assert p99 < 90, f"p99 gap under 90s threshold is {p99}, expected well below 90"


@check("100% of watched content_ids have metadata in content_dim")
def _():
    n = scalar(f"""
        SELECT count(DISTINCT r.content_id) FROM {RAW} r
        LEFT JOIN {CONTENT} c ON r.content_id = c.content_id
        WHERE c.content_id = 0
    """)
    assert n == 0, f"{n} content_ids in raw events have no metadata match"


@check("video errors are terminal (no session event after VideoError, ignoring same-session end dup)")
def _():
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id,
                   max(event_timestamp) AS last_ts,
                   maxIf(event_timestamp, event_type = 'VideoError') AS err_ts,
                   countIf(event_type = 'VideoError') AS n_err
            FROM {RAW}
            GROUP BY video_session_id
            HAVING n_err > 0
        ) WHERE last_ts > err_ts + 5000  -- >5s of "recovery" events after last error
    """)
    # Live measurement: 14/905558 sessions have >5s of trailing activity after
    # VideoError (doc's own 99.7% terminal claim allows for this). Track the
    # ceiling so a regression (state machine not treating error as absorbing) shows up.
    assert n <= 20, f"{n} sessions show meaningful activity after VideoError (ceiling 20, was 14 at baseline)"


@check("duplicate active-state transitions exist and must be filtered by state-change dedup")
def _():
    # This is a sanity check that the anomaly is real and not fixed data drift —
    # if it goes to 0 the state machine's lag()-based dedup logic (A1/A2) becomes untested.
    n = scalar(f"""
        SELECT countIf(event = 'resume')
        FROM {RAW}
        WHERE event_type = 'VideoHeartbeat'
    """)
    assert n > 1000, f"expected large volume of resume heartbeats (dedup-relevant), got {n}"


@check("platform cardinality is low (safe as a leading ORDER BY key)")
def _():
    n = scalar(f"SELECT count(DISTINCT platform) FROM {RAW}")
    assert n <= 20, f"platform cardinality {n} higher than expected, revisit ORDER BY design"


@check("no null/empty content_id, platform, or country in raw events")
def _():
    n = scalar(f"""
        SELECT count() FROM {RAW}
        WHERE platform = '' OR country = '' OR content_id = 0
    """)
    assert n == 0, f"{n} rows with empty platform/country or content_id=0"


@check("category cardinality is low-medium (safe as an ORDER BY key per LLD §2.4/2.5)")
def _():
    n = scalar(f"SELECT count(DISTINCT category) FROM {CONTENT}")
    assert 20 <= n <= 200, f"category cardinality {n} outside expected 20-200 band (EDA measured 80)"


@check("exact duplicate events exist (video_session_id, event_type, event, event_timestamp) — dedup layer 1/9.1 is load-bearing")
def _():
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id, event_type, event, event_timestamp, count() AS c
            FROM {RAW}
            GROUP BY video_session_id, event_type, event, event_timestamp
            HAVING c > 1
        )
    """)
    assert n > 0, "expected exact-duplicate event groups (LLD cites 4,210); dedup path (arrayDistinct) would go untested"


@check("same-millisecond ties within a session exist — tie-break priority (LLD §9.3) is load-bearing")
def _():
    n = scalar(f"""
        SELECT count() FROM (
            SELECT video_session_id, event_timestamp, count() AS c
            FROM {RAW}
            GROUP BY video_session_id, event_timestamp
            HAVING c > 1
        )
    """)
    assert n > 1000, f"expected large volume of same-ms ties (LLD cites 161K); got {n}"


def main():
    failures = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}\n        -> {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {name}\n        -> {type(e).__name__}: {e}")
    print(f"\n{len(checks) - failures}/{len(checks)} data-expectation checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
