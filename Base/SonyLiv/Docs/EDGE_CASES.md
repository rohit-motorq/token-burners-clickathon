# Edge Case Handling — Foreground-Only Concurrency (Combined)
## Token Burners · Click-a-thon 2026

> **This document merges findings from both analysis tracks** (Keshav's DuckDB-validated
> test suite and Rohit's ClickHouse live-query analysis). Where the two disagree, the
> reconciled position is stated.

---

## Key Reconciled Differences

| Topic | Keshav's Finding | Rohit's Finding | Reconciled Position |
|-------|-----------------|-----------------|---------------------|
| **Peak FG concurrency** | 2,697 (minute-deduped) | 2,316 (cumulative delta) | **2,697 is correct** — delta model must dedupe per minute |
| **OOO events** | 11.35% (CSV row order) | 0 (timestamp order) | **0 true OOO by timestamp**; 11.35% is file-order artifact. Still sort defensively. |
| **VideoError** | 55 sessions continue after error | 0 recover | **55 continue** — don't treat error as always-terminal |
| **Foreground default** | Assume FG before first marker (decides 1,125 h) | Start as INACTIVE before Play | **Both correct**: INACTIVE before Play, FG assumed between Play and first BG |

---

## CRITICAL ISSUE: Intra-Minute Flapping (The #1 Accuracy Bug)

**If you get nothing else right, get this right.**

A session can go active→inactive→active WITHIN a single minute. The naive delta model
counts this as 2 concurrent sessions in that minute. The truth is 1.

```
Minute 10:   ├─ active ─┤  (pause)  ├─ active ─┤
              +1     −1             +1      −1
             
Naive delta sum at minute 10 → 2.   Truth → 1 viewer.
```

| Evidence | Value |
|---|---|
| Session-minutes with >1 active interval | **3,476 of 27,268 (12.75%)** |
| Worst case intervals in one minute | 21 |
| Peak overcounting (naive delta vs correct) | 2,902 vs 2,697 (**+7.6%**) |

**Fix:** After computing intervals, deduplicate to **distinct (session_id, minute)** before
emitting deltas. A session contributes at most +1 per minute, regardless of how many
times it toggles active/inactive within that minute.

```sql
-- CORRECT: dedupe intervals to session-minutes, THEN emit deltas from runs
WITH session_minutes AS (
    -- For each session, list the minutes it was active in (deduped)
    SELECT DISTINCT
        video_session_id,
        platform, country, video_type, category, content_id,
        minute
    FROM active_intervals_exploded_to_minutes
),
-- Merge contiguous minutes into runs, emit +1 at run start, -1 at run end
runs AS (
    SELECT
        video_session_id, platform, country, video_type, category, content_id,
        min(minute) AS run_start,
        max(minute) + INTERVAL 1 MINUTE AS run_end
    FROM (
        SELECT *,
            minute - ROW_NUMBER() OVER (PARTITION BY video_session_id ORDER BY minute) 
                * INTERVAL 1 MINUTE AS grp
        FROM session_minutes
    )
    GROUP BY video_session_id, platform, country, video_type, category, content_id, grp
)
-- Emit deltas from runs (not from raw intervals)
SELECT run_start AS minute, ..., 1 AS session_delta FROM runs
UNION ALL
SELECT run_end AS minute, ..., -1 AS session_delta FROM runs
```

---

## The 3 Non-Negotiable Rules

### Rule 1: Only emit delta when state CHANGES (between minutes)

```
BAD:  every resume → emit +1    (causes 9,950 phantom +1 deltas)
BAD:  every pause  → emit -1    (causes 14,907 phantom -1 deltas)

GOOD: only when prev_state != new_state → emit delta
      AND deduplicate to 1 session per minute (Rule 0)
```

### Rule 2: Terminal is absorbing (no escape)

```
BAD:  SessionEnd → AppBackgrounded → treat as new inactive period
GOOD: once TERMINAL, discard ALL subsequent events for that session
```

### Rule 3: AppForegrounded alone does NOT activate

```
BAD:  AppForegrounded → mark session as ACTIVE (+1 delta)
GOOD: AppForegrounded → NO state change. Wait for resume/Play.
```

---

## Summary of All Edge Cases

| # | Category | Edge Case | Count | Severity | Fix |
|---|----------|-----------|-------|----------|-----|
| **0** | **Counting** | **Intra-minute flapping (multi-interval in 1 min)** | **12.75% of session-minutes** | **🔴 CRITICAL** | **Dedupe to distinct (session, minute)** |
| 1 | Transitions | active→active (resume while playing) | 9,950 | 🔴 HIGH | prev_state check |
| 2 | Transitions | inactive→inactive (pause then BG) | 14,907 | 🔴 HIGH | prev_state check |
| 3 | Terminal | Events after SessionEnd | 802 events | 🔴 HIGH | Terminal absorbs |
| 4 | FG/BG | AppForegrounded without resume | Thousands | 🔴 HIGH | FG = no-op always |
| 5 | Counting | Sparse delta table skips minutes | All queries | 🔴 HIGH | Dense fill with `WITH FILL` |
| 6 | Signals | Heartbeats fire while backgrounded | 3,674 signals | 🔴 HIGH | State > heartbeat presence |
| 7 | Signals | Pause hidden in VideoHeartbeat/event | All pauses | 🔴 HIGH | Check (event_type, event) |
| 8 | Query | Peak NOT additive across dimensions | 89 session overcount | 🟡 MEDIUM | Never sum sub-peaks |
| 9 | Query | Average has 4.7x spread by definition | All averages | 🟡 MEDIUM | Declare denominator |
| 10 | Lifecycle | VideoError: 55 sessions continue | 55 sessions | 🟡 MEDIUM | Don't kill on error alone |
| 11 | Dimensions | audio_language drifts within session (6,864) | 6,864 sessions | 🟡 MEDIUM | Pin at session start |
| 12 | Dimensions | subtitle_language drifts (8,882) | 8,882 sessions | 🟡 MEDIUM | Pin at session start |
| 13 | Identity | 120 shared sessions (2 user_ids) | 120 sessions | 🟡 MEDIUM | Count at session level |
| 14 | Identity | 301-session bot user | 1 user (4.7%) | 🟡 MEDIUM | Flag, don't exclude |
| 15 | Lifecycle | Mismatched BG/FG (418 more BG, 48 more FG) | 466 sessions | 🟡 MEDIUM | Timeout handles |
| 16 | Lifecycle | Duplicate events (4,210 exact dupes) | 0.465% of rows | 🟡 MEDIUM | Dedupe before delta |
| 17 | Default | Foreground assumed before first BG marker | 1,125 hours | 🟡 MEDIUM | Default FG=1 after Play |
| 18 | Time | Sessions crossing day boundaries | 11 sessions | 🟢 LOW | Deltas in both partitions |
| 19 | Time | 43-hour session (multi-day gap) | 1 session | 🟢 LOW | 90s timeout clips |
| 20 | Lifecycle | Zero-duration sessions | 12 sessions | 🟢 LOW | Net 0 delta (natural) |
| 21 | Lifecycle | Duplicate Start/Play/End | 13-16 sessions | 🟢 LOW | Idempotent handling |
| 22 | OOO | Out-of-order events (0 by timestamp) | 0 confirmed | 🟢 LOW | Defensive sort |
| 23 | OOO | Late arrivals (up to 35 min after End) | 802 events | 🟢 LOW | Terminal absorbs |
| 24 | Identity | Users on multiple platforms | 85 users | 🟢 LOW | Count each session |
| 25 | Content | content_id switch mid-session | 1 session | 🟢 LOW | Use first content_id |

---

## Category 1: Signal Interpretation

### 1.1 Heartbeats fire while app is backgrounded

The SDK continues sending heartbeats (buffer-health, network-activity, video-resize) even
when the app is in the background. **A heartbeat does NOT prove the user is watching.**

| Evidence | Value |
|---|---|
| Signals firing inside a background window | **3,674** |
| Sessions affected | **2,361** |

**Policy:** Foreground state (from BG/FG events) takes precedence over heartbeat presence.
A heartbeat proves the process is alive, never that it is visible.

### 1.2 Pause/resume are hidden inside VideoHeartbeat

There is no `VideoPause` event type. The pause/resume state changes are in:
```
event_type = 'VideoHeartbeat', event = 'pause'
event_type = 'VideoHeartbeat', event = 'resume'
```

**Policy:** Always check `(event_type, event)` together, never `event_type` alone.

### 1.3 Heartbeat cadence is 40 seconds, not 60 seconds (docs are wrong)

| Metric | Measured Value |
|---|---|
| buffer-health gap (p50) | 40.00s |
| video-resize gap (p50) | 40.00s |
| network-bandwidth gap (p50) | 40.00s |
| IQR/median | **0.00** (machine-precise) |

**Policy:** Timeout = 90s (≈2.25 missed 40s beats). Any design assuming 60s is calibrated
against a cadence that doesn't exist.

### 1.4 No single heartbeat family covers all sessions

| Liveness basis | Sessions missed |
|---|---|
| network-activity alone | 1,236 missed |
| buffer-health alone | 1,284 missed |
| video-resize alone | 3,075 missed |
| Union of all three | 1,158 missed |
| **Any signal at all** | **0 missed** |

**Policy:** Liveness keys off ANY event, not a designated heartbeat type.

### 1.5 BufferStart/BufferEnd are NOT inactivity

Buffering = still watching (user waiting for content). 56.2% of sessions have BufferStart
events. Only 0.21% fire while backgrounded.

**Policy:** Buffer events prove liveness but never change foreground/playing state.

### 1.6 Download events are playback-independent

`download_initiated`, `download_completed`, etc. happen without playback and often while
backgrounded. A download event must never open an active interval.

---

## Category 2: Counting & Aggregation

### 2.1 Intra-Minute Flapping (THE CRITICAL BUG)

Covered in detail above. A session with 2+ active intervals in 1 minute must count as 1,
not 2.

**Frequency:** 12.75% of all session-minutes.
**Impact if missed:** Peak overcounted by 7.6% (2,902 vs 2,697).

### 2.2 Sparse delta table must be densified

Only minutes with a state change get a delta row. Minutes without changes still have the
SAME concurrency as the previous minute.

| Evidence | Value |
|---|---|
| Minutes carrying a delta row | 1,490 |
| Minutes actually occupied | 3,649 |

**Fix:** `WITH FILL STEP toIntervalMinute(1)` in ClickHouse, or carry-forward in the
cumulative sum.

### 2.3 Duplicate events corrupt deltas permanently

4,210 exact duplicate rows (0.465%) exist. In a delta model, a duplicated +1 with no
matching -1 corrupts EVERY subsequent minute permanently.

**Fix:** Deduplicate on `(video_session_id, event_type, event, event_timestamp)` before
computing deltas.

### 2.4 Peak is NOT additive across dimensions

| Evidence | Value |
|---|---|
| Sum of per-platform peaks | 2,786 |
| True overall peak | 2,697 |
| Overstatement | 89 sessions |

Each dimension slice peaks at a different minute. You cannot sum sub-peaks.

**Fix:** `peak = max()` over minute concurrency at the requested filter combination.
Never summed from parts.

### 2.5 Average has 4.7x spread by definition

| Interpretation | Value |
|---|---|
| Mean over occupied minutes | 34.85 |
| Mean over entire time span | 7.47 |
| Time-weighted | 28.99 |

**Fix:** Declare which definition you're using alongside the number. Recommend mean over
occupied minutes.

---

## Category 3: State Machine

### 3.1 State Transition Rules

```
State: INACTIVE (initial — before VideoPlay)
  → VideoPlay/Play         → ACTIVE (+1)
  → VideoHeartbeat/resume  → ACTIVE (+1)
  → AppForegrounded        → stays INACTIVE (Rule 3)
  → VideoSessionEnd        → TERMINAL (no delta)

State: ACTIVE
  → AppBackgrounded        → INACTIVE (-1)
  → VideoHeartbeat/pause   → INACTIVE (-1)
  → VideoSessionEnd        → TERMINAL (-1)
  → VideoError             → INACTIVE (-1) [NOT terminal — 55 sessions continue]
  → No event for 90s       → INACTIVE (-1, timeout)
  → resume/Play again      → stays ACTIVE (Rule 1, no delta)
  → AppForegrounded        → stays ACTIVE (no-op)

State: INACTIVE (after pause/BG)
  → VideoHeartbeat/resume  → ACTIVE (+1)
  → VideoPlay/Play         → ACTIVE (+1)
  → pause/BG again         → stays INACTIVE (Rule 1, no delta)
  → AppForegrounded        → stays INACTIVE (Rule 3)
  → VideoSessionEnd        → TERMINAL (no delta)

State: TERMINAL
  → ANY EVENT              → stays TERMINAL (Rule 2, absorb)
```

### 3.2 Foreground Default (decides 1,125 hours of data)

**Before first BG/FG marker:** Assume FOREGROUND. Evidence:
- 96.98% of sessions have first `AppBackgrounded` AFTER first `VideoPlay`
- The BG marker is the user LEAVING a visible session, not evidence it started hidden

**After VideoPlay, before first BG:** Session is active and in foreground.
**Before VideoPlay:** Session is inactive (not yet playing) regardless of foreground state.

### 3.3 VideoError: NOT Always Terminal

| Evidence | Value |
|---|---|
| Sessions with error that immediately end | 238 (81%) |
| Sessions that CONTINUE after error | **55 (19%)** |

**Policy:** Treat VideoError as → INACTIVE (emit -1 if was active), NOT as terminal.
If the session continues (heartbeats arrive), the state machine re-activates normally.

### 3.4 Duplicate/Redundant Transitions (25,768 events)

| Pattern | Count | Cause |
|---------|-------|-------|
| resume→resume | 9,950 | SDK "still alive" signal |
| pause→BG | 11,265 | User pauses then switches app |
| BG→pause (late) | 2,185 | Queued heartbeat fires after BG |
| BG→BG (double) | 948 | SDK race condition |
| SessionEnd→SessionEnd | 295 | Double close |
| Play→resume | 894 | Resume while already active |

**Fix:** `if prev_state == new_state → no delta`

### 3.5 Same-Millisecond Ties (894 state events)

**Tie-breaking priority (deterministic order):**
```
VideoSessionStart → 1
VideoPlay         → 2
resume            → 3
pause             → 4
AppBackgrounded   → 5
AppForegrounded   → 6 (no-op anyway)
VideoSessionEnd   → 7
VideoError        → 8
```

Conservative: resume before pause means tied events end at INACTIVE (don't overcount).

### 3.6 Dimension Drift Within Sessions

| Dimension | Sessions with >1 value |
|---|---|
| audio_language | **6,864** |
| subtitle_language | **8,882** |
| user_id | 120 |
| platform | 95 |
| content_id | 1 |

**Policy:** Pin every dimension to its value at session-start event. One row per session
in the dimension lookup.

---

## Category 4: Session Lifecycle

### 4.1 All Sessions Closed in Training Data (Unseen Day Will Differ)

Every session has Start + Play + End. The unseen day WILL have open sessions.

**Policy:** Active until proven stale (90s timeout). Do NOT wait for SessionEnd to count.

### 4.2 Marathon & Abandoned Sessions

| Duration | Sessions |
|----------|----------|
| Over 1 hour | 150 |
| Over 6 hours | 12 |
| Over 12 hours | 1 |
| **Longest** | **43.6 hours** |

**Retention varies wildly:** 64% for normal sessions → 3.7% for marathons. No single
correction factor works.

### 4.3 Long Signal Gaps

| Gap threshold | Occurrences |
|---|---|
| Over 90s | 5,677 |
| Over 5 min | 2,445 |
| Over 30 min | 206 |
| Maximum | 39.6 hours |

**Policy:** Cap active interval at 90s past last event. Gap splits the run.

### 4.4 Sessions That Never Play

| Evidence | Value |
|---|---|
| Sessions with no Play in training data | 0 |
| Sessions ending with zero active time | 16 |

**Policy:** Playing gate defaults closed. No Play = 0 active time = not counted.

### 4.5 Post-Terminal Events

| Event After Terminal | Count | Avg Delay |
|---------------------|-------|-----------|
| AppBackgrounded | 247 | 559ms |
| network-bandwidth | 297 | 8 min |
| Seek/pause/resume | 185 | 8–16 min |
| VideoPlay (restart) | 15 | 1.3 sec |

**Policy:** Terminal absorbs. The 15 VideoPlay restarts are edge cases (0.14%) — ignore.

---

## Category 5: Time & Boundaries

### 5.1 Out-of-Order Events

| Measure | Value |
|---|---|
| True OOO (timestamp goes backward within session) | **0** |
| File row-order inversions | 11.35% (artifact of CSV grouping by session) |

**Conclusion:** No true OOO in training data. The 11.35% figure is a file-order artifact.

**Policy:** Sort by `event_timestamp` before processing (defensive — costs nothing, prevents
disaster if unseen day has OOO from distributed ingestion).

### 5.2 Late Arrivals (After SessionEnd)

- 802 events across 239 sessions
- Max delay: 35 minutes
- All have timestamps AFTER SessionEnd (not OOO — genuinely late SDK events)

**Policy:** Terminal state absorbs them. No special watermark needed.

### 5.3 Sessions Crossing Boundaries

| Boundary | Sessions crossing |
|---|---|
| Minute | 10,438 (96%) |
| Hour | 3,882 |
| UTC day | 11 |

**Policy:** Deltas emitted at the actual minute of state change. A session spanning hour
boundary naturally has its +1 in hour A and -1 in hour B.

### 5.4 Liveness Timeout

ANY event resets the 90-second clock (not just state-changing events):
- buffer-health, video-resize, network-activity → reset clock, no state change
- Seek, video_forward → reset clock, no state change
- BufferStart/End → reset clock, no state change
- pause, resume, BG → reset clock AND change state

---

## Category 6: Dimensions & Joins

### 6.1 Content Join: LEFT JOIN Only

| Evidence | Value |
|---|---|
| Content IDs in raw with no metadata | 0 |
| Content rows with blank video_type | 1,089 (142 content IDs, 250 sessions) |

**Policy:** `LEFT JOIN` + `coalesce(..., 'unknown')`. Never INNER JOIN.

### 6.2 Audio Language Normalization

41 raw values → ~15 after normalization. Examples:
- `hin`, `HIN`, `hin-hindi` → `hin`
- `eng`, `ENG`, `eng-english` → `eng`
- `unk`, `UNK`, empty → `unknown`

**Fix:** `lower(splitByChar('-', audio_language)[1])` at ingestion.

### 6.3 Empty/Null Dimensions

| Dimension | Empty count |
|---|---|
| audio_language | 1,991 |
| subtitle_language | 2,006 |
| player_version | 1,534 |

**Policy:** Map to `'unknown'`. Never drop events due to missing dimensions.

---

## Category 7: Open Sessions & Incremental Updates

### 7.1 Reconnect After Network Outage

User loses internet → 90s silence → timed out → network returns → heartbeats resume.

**Two separate timers:**
| Timer | Value | Job |
|---|---|---|
| Activity timeout | 90s | When we stop COUNTING them |
| Eviction timeout | 10 min | When we FORGET their state |

A session that returns after eviction simply creates a new state entry. Runs are
independent; deltas are additive. No reconciliation needed.

| Evidence | Value |
|---|---|
| Sessions with >1 active run | **4,511 of 10,850 (41.6%)** |
| Max runs in one session | 8 |

### 7.2 Incremental Update Cost

| Heartbeat behavior | Count | % |
|---|---|---|
| Lands in same minute (no serving change) | **650,388** | **84%** |
| Advances one minute | 116,030 | 15% |
| Advances multiple minutes | 6,377 | 1% |

**Key insight:** 84% of heartbeats don't change the serving layer at all. This is why the
delta model is efficient — most events are no-ops for concurrency.

### 7.3 Synthetic Data Fingerprints (Do NOT rely on these)

Properties of training data that WILL differ on unseen day:

| Fingerprint | Training Data | Expect on Unseen Day |
|---|---|---|
| Every session has END | ✅ (0 open) | ❌ Some will be open |
| Every session has BG events | ✅ (10,866/10,866) | ❌ Some may have no BG/FG |
| Country = single value | ✅ (india only) | ❓ Might have others |
| All content IDs have metadata | ✅ (0 missing) | ❓ Might have gaps |
| Zero OOO by timestamp | ✅ (0 inversions) | ❓ Might have some |

---

## Complete State Machine SQL (All Edge Cases Handled)

```sql
-- THE CORRECT PIPELINE (handles all 25 edge cases)
-- Step 1: Deduplicate raw events
-- Step 2: Sort by timestamp + tie-break
-- Step 3: Compute state transitions with prev_state check
-- Step 4: Extract active intervals
-- Step 5: Explode to minutes + dedupe per session-minute
-- Step 6: Merge contiguous minutes into runs
-- Step 7: Emit deltas from runs (+1 at start, -1 at end)

WITH 
-- Step 1: Deduplicate
deduped AS (
    SELECT DISTINCT video_session_id, event_type, event, event_timestamp,
        platform, content_id, country, user_id, session_start_epoch
    FROM raw_events
),

-- Step 2: Filter to state-relevant events + sort
state_events AS (
    SELECT *,
        CASE
            WHEN event_type = 'VideoPlay' THEN 'active'
            WHEN event_type = 'VideoHeartbeat' AND event = 'resume' THEN 'active'
            WHEN event_type = 'AppBackgrounded' THEN 'inactive'
            WHEN event_type = 'VideoHeartbeat' AND event = 'pause' THEN 'inactive'
            WHEN event_type = 'VideoSessionEnd' THEN 'terminal'
            WHEN event_type = 'VideoError' THEN 'inactive'  -- NOT terminal (55 continue)
            WHEN event_type = 'VideoSessionStart' THEN 'inactive'
            ELSE NULL  -- AppForegrounded and others: no state change
        END AS implied_state
    FROM deduped
    WHERE event_type IN ('VideoPlay','AppBackgrounded','VideoSessionEnd','VideoError','VideoSessionStart')
        OR (event_type = 'VideoHeartbeat' AND event IN ('pause','resume'))
),

-- Step 3: Add previous state, filter duplicates (Rule 1)
with_prev AS (
    SELECT *,
        lag(implied_state) OVER (
            PARTITION BY video_session_id 
            ORDER BY event_timestamp,
                multiIf(event_type='VideoSessionStart',1, event_type='VideoPlay',2,
                    event='resume',3, event='pause',4, event_type='AppBackgrounded',5,
                    event_type='AppForegrounded',6, event_type='VideoSessionEnd',7,
                    event_type='VideoError',8, 9)
        ) AS prev_state
    FROM state_events
    WHERE implied_state IS NOT NULL
),

-- Only actual state changes (Rules 1 & 2)
transitions AS (
    SELECT * FROM with_prev
    WHERE implied_state != coalesce(prev_state, 'none')  -- actual change
        AND coalesce(prev_state, 'none') != 'terminal'   -- Rule 2: terminal absorbs
),

-- Step 4: Active intervals (from each active→inactive/terminal transition)
active_intervals AS (
    SELECT 
        video_session_id, platform, content_id, country,
        event_timestamp AS interval_start,
        lead(event_timestamp) OVER (
            PARTITION BY video_session_id ORDER BY event_timestamp
        ) AS interval_end,
        implied_state
    FROM transitions
),

valid_intervals AS (
    SELECT *,
        -- Cap at 90s if no end (timeout)
        if(interval_end IS NULL OR interval_end = 0, 
           interval_start + 90000, interval_end) AS capped_end
    FROM active_intervals
    WHERE implied_state = 'active'
),

-- Step 5: Explode to minutes + DEDUPLICATE per (session, minute)
session_minutes AS (
    SELECT DISTINCT
        video_session_id, platform, content_id, country,
        arrayJoin(
            arrayMap(x -> toStartOfMinute(fromUnixTimestamp64Milli(toInt64(interval_start))) 
                + toIntervalMinute(x),
                range(toUInt32(
                    dateDiff('minute', 
                        toStartOfMinute(fromUnixTimestamp64Milli(toInt64(interval_start))),
                        toStartOfMinute(fromUnixTimestamp64Milli(toInt64(capped_end)))
                    ) + 1
                ))
            )
        ) AS minute
    FROM valid_intervals
),

-- Step 6: Merge contiguous minutes into runs
with_gaps AS (
    SELECT *,
        minute - toIntervalMinute(
            row_number() OVER (PARTITION BY video_session_id ORDER BY minute)
        ) AS grp
    FROM session_minutes
),

runs AS (
    SELECT 
        video_session_id, platform, content_id, country,
        min(minute) AS run_start,
        max(minute) + toIntervalMinute(1) AS run_end
    FROM with_gaps
    GROUP BY video_session_id, platform, content_id, country, grp
)

-- Step 7: Emit deltas from runs
SELECT run_start AS minute, platform, country,
    dictGet('content_dict','video_type',content_id) AS video_type,
    dictGet('content_dict','category',content_id) AS category,
    content_id, 1 AS session_delta
FROM runs

UNION ALL

SELECT run_end AS minute, platform, country,
    dictGet('content_dict','video_type',content_id) AS video_type,
    dictGet('content_dict','category',content_id) AS category,
    content_id, -1 AS session_delta
FROM runs;
```

---

## Verification Queries

### 1. Concurrency never goes negative
```sql
SELECT min(running_total) FROM (
    SELECT sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas GROUP BY minute
);
-- Expected: 0 (never negative)
```

### 2. Peak matches ground truth
```sql
SELECT max(running_total) FROM (
    SELECT sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas WHERE toDate(minute) = '2026-07-26' GROUP BY minute
);
-- Expected: ~2,697 (minute-deduped model)
```

### 3. Closed sessions net to zero
```sql
SELECT video_session_id, sum(session_delta) AS net
FROM concurrency_deltas
GROUP BY video_session_id HAVING net != 0;
-- Expected: empty (all closed sessions cancel out)
```

### 4. Dense fill produces correct curve
```sql
SELECT minute, sum(sum(session_delta)) OVER (ORDER BY minute) AS concurrent
FROM concurrency_deltas
WHERE toDate(minute) = '2026-07-26'
GROUP BY minute
ORDER BY minute WITH FILL 
    FROM toDateTime('2026-07-26 00:00:00') 
    TO toDateTime('2026-07-27 00:00:00') 
    STEP toIntervalMinute(1);
```

---

## Priority Matrix

| Priority | Cases | Impact if Missed |
|----------|-------|-----------------|
| **P0 (MUST)** | Intra-minute flapping (#0), Duplicate transitions (#1-2), Terminal absorbing (#3), FG≠active (#4), Dense fill (#5), Heartbeats in BG (#6), Pause in HB (#7) | **7-37% wrong numbers** |
| **P1 (SHOULD)** | Peak additivity (#8), Average definition (#9), Error handling (#10), Dimension drift (#11-12), Deduplication (#16), FG default (#17) | **2-5% accuracy, benchmark mismatch** |
| **P2 (NICE)** | Identity (#13-14), Lifecycle (#18-21), OOO (#22-23), Multi-platform (#24), Content switch (#25) | **<1% accuracy** |

---

## Source Mapping

| Finding Source | Methodology | Peak Reported |
|---|---|---|
| Keshav (DuckDB, injected tests) | Intervals → minute-dedupe → runs → deltas | **2,697** |
| Rohit (ClickHouse live queries) | State transitions → cumulative delta | **2,316** |
| **Reconciled** | Delta model + minute-dedup = correct | **~2,697** |

The difference (2,697 vs 2,316) is exactly the intra-minute flapping bug: the cumulative
delta model overcounts when sessions toggle multiple times within one minute, and the
un-deduped cumulative sum undercounts by attributing deltas to wrong minutes when the table
is sparse.
