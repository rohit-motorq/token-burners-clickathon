#!/usr/bin/env python3
"""
Ground-truth foreground concurrency, computed independently of the SQL
pipeline (HLD/DataFlow docs) by folding raw events in Python session-by-
session. This is the "data expectation" the benchmark queries are checked
against — not the private judge answer key, but a from-scratch second
implementation of the same spec (§10 of DATA_ANALYSIS-ROHIT.md), so a bug
shared by both would have to be a bug in the spec itself, not the SQL.

State machine (per session), matching HLD:
  fg      default 1, AppForegrounded->1, AppBackgrounded->0
  playing default 0, VideoPlay/resume->1, pause->0
  ended   VideoError or VideoSessionEnd -> terminal, absorbing (later events ignored)
  counted = fg=1 AND playing=1 AND ended=0

Active interval closes either when counted flips 0 (explicit event) or when
silence exceeds 90s while counted=1 (heartbeat-missing case) — the run is
cut at last_active_ts + 90s, matching the staleness sweep semantics.

Duplicate active->active / inactive->inactive transitions (A1/A2 in the EDA)
naturally collapse here because we only emit a delta when counted changes.

Output: per-minute delta rows (+1/-1) with platform/country/video_type/
content_id, written to CSV for reuse by test_benchmarks.py.
"""
import csv
import sys
from collections import defaultdict

from ch_client import query
from table_names import RAW_EVENTS as RAW, CONTENT_DIM as CONTENT

TIMEOUT_MS = 90_000

PLAY_ON = {"VideoPlay"}  # event_type
RESUME_EVENT = "resume"
PAUSE_EVENT = "pause"
FG_ON = "AppForegrounded"
FG_OFF = "AppBackgrounded"
END_TYPES = {"VideoSessionEnd", "VideoError"}


def fetch_events():
    rows = query(f"""
        SELECT r.video_session_id AS sid, r.user_id AS user_id, r.event_timestamp AS ts,
               r.event_type AS event_type, r.event AS event,
               r.platform AS platform, r.country AS country,
               r.content_id AS content_id,
               c.video_type AS video_type
        FROM {RAW} r
        LEFT JOIN {CONTENT} c ON r.content_id = c.content_id
        ORDER BY r.video_session_id, r.event_timestamp
    """)
    return rows


def fold_session(events):
    """events: list of dicts for one session, already time-sorted.
    Returns list of (start_ts, end_ts) active intervals (ms epoch)."""
    fg, playing, ended = 1, 0, False
    counted = False
    run_start = None
    intervals = []
    dims = None

    for i, e in enumerate(events):
        if dims is None:
            dims = (e["platform"], e["country"], e["content_id"], e["video_type"], e["user_id"])
        if ended:
            break  # absorbing terminal state — ignore trailing events

        ts = int(e["ts"])
        etype, ev = e["event_type"], e["event"]

        # close a run that went silent >90s before this event, at the timeout mark
        if counted and run_start is not None:
            prev_ts = events[i - 1]["ts"] if i > 0 else run_start
            if ts - prev_ts > TIMEOUT_MS:
                intervals.append((run_start, prev_ts + TIMEOUT_MS))
                counted = False
                run_start = None

        # apply event to switches
        if etype == FG_ON:
            fg = 1
        elif etype == FG_OFF:
            fg = 0
        elif etype in PLAY_ON or ev == RESUME_EVENT:
            playing = 1
        elif ev == PAUSE_EVENT:
            playing = 0
        elif etype in END_TYPES:
            ended = True

        new_counted = (fg == 1 and playing == 1 and not ended)
        if new_counted and not counted:
            run_start = ts
        elif not new_counted and counted:
            intervals.append((run_start, ts))
            run_start = None
        counted = new_counted

    # trailing open run: close at last event + timeout (matches sweep — no
    # further proof of life exists in a closed historical dataset)
    if counted and run_start is not None:
        last_ts = events[-1]["ts"]
        intervals.append((run_start, last_ts + TIMEOUT_MS))

    return intervals, dims


def build_deltas():
    events = fetch_events()
    by_session = defaultdict(list)
    for e in events:
        by_session[e["sid"]].append(e)

    # minute -> (platform, country, video_type, content_id) -> delta
    deltas = defaultdict(lambda: defaultdict(int))
    for sid, evs in by_session.items():
        intervals, dims = fold_session(evs)
        if dims is None:
            continue
        delta_dims = dims[:4]  # platform, country, content_id, video_type — no user_id
        for start_ts, end_ts in intervals:
            start_min = (start_ts // 1000 // 60) * 60
            end_min = (end_ts // 1000 // 60) * 60
            deltas[start_min][delta_dims] += 1
            deltas[end_min][delta_dims] -= 1
    return deltas


def write_csv(deltas, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute_epoch", "platform", "country", "content_id", "video_type", "delta_sessions"])
        for minute, dim_map in deltas.items():
            for (platform, country, content_id, video_type), delta in dim_map.items():
                if delta != 0:
                    w.writerow([minute, platform, country, content_id, video_type, delta])


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "reference_deltas.csv"
    deltas = build_deltas()
    write_csv(deltas, out)
    print(f"wrote reference deltas -> {out}")
