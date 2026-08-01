# Edge Case Handling — Foreground-Only Concurrency
## Token Burners · Click-a-thon 2026

---

## Summary

| Category | Edge Cases | Count in Data | Risk if Missed |
|----------|-----------|---------------|----------------|
| Duplicate Transitions | 5 cases | 25,768 events | 🔴 Concurrency goes negative or spikes |
| Terminal State | 4 cases | 538 events | 🔴 Dead sessions resurrect |
| Session Lifecycle | 4 cases | 42 sessions | 🟡 Minor overcounting |
| Foreground/Background | 5 cases | 11,762 events | 🔴 Massive overcounting |
| Timeout/Heartbeat | 4 cases | All sessions | 🔴 Never-ending sessions |
| User/Session Identity | 4 cases | 506 sessions | 🟡 User-level inaccuracy |
| Timestamp/Ordering | 4 cases | 894 events | 🟡 Race conditions |
| Content/Dimensions | 3 cases | ~2,250 events | 🟢 Filter inaccuracy |

**Total: 33 distinct edge cases. 3 are non-negotiable (P0).**

---

## NON-NEGOTIABLE: The 3 Rules That Cannot Be Broken

### Rule 1: Only emit delta when state CHANGES

```
BAD:  every resume → emit +1    (causes 9,950 phantom +1 deltas)
BAD:  every pause  → emit -1    (causes 14,907 phantom -1 deltas)

GOOD: only when prev_state != new_state → emit delta
```

### Rule 2: Terminal is absorbing (no escape)

```
BAD:  SessionEnd → AppBackgrounded → treat as new inactive period
BAD:  SessionEnd → Play → treat as re-activation

GOOD: once TERMINAL, discard ALL subsequent events for that session
```

### Rule 3: AppForegrounded alone does NOT activate

```
BAD:  AppForegrounded → mark session as ACTIVE (+1 delta)

GOOD: AppForegrounded → NO state change. Wait for resume/Play.
```

---

## Category 1: Duplicate/Redundant Transitions

### Edge Case 1.1 — active → active (resume while already playing)

**What happens:** SDK fires `resume` as a periodic "still alive" signal, even when user never paused.

**Example sequence:**
```
VideoPlay/Play          → state: ACTIVE  → emit +1
VideoHeartbeat/resume   → state: ACTIVE  → NO DELTA (already active)
VideoHeartbeat/resume   → state: ACTIVE  → NO DELTA
VideoHeartbeat/resume   → state: ACTIVE  → NO DELTA
```

**Frequency:** 9,950 occurrences across 1,980 sessions

**Impact if missed:** Each extra resume emits a phantom +1. At peak, concurrency would be inflated by hundreds.

**Fix:** `if prev_state == 'active' AND new_state == 'active' → skip`

---

### Edge Case 1.2 — inactive → inactive (pause then background)

**What happens:** User pauses the video, then switches to another app. Both mean "not watching" but SDK fires both.

**Example sequence:**
```
VideoHeartbeat/pause    → state: INACTIVE → emit -1
AppBackgrounded         → state: INACTIVE → NO DELTA (already inactive)
```

**Frequency:** 11,265 occurrences (most common duplicate)

**Impact if missed:** Double -1 makes concurrency go NEGATIVE for that dimension.

**Fix:** `if prev_state == 'inactive' AND new_state == 'inactive' → skip`

---

### Edge Case 1.3 — inactive → inactive (background then late pause)

**What happens:** App backgrounds first, then a heartbeat with `pause` arrives milliseconds later (was already queued in SDK pipeline).

**Example:** `AppBackgrounded → VideoHeartbeat/pause`

**Frequency:** 2,185 occurrences

**Fix:** Same prev_state check.

---

### Edge Case 1.4 — inactive → inactive (double background)

**What happens:** App fires `AppBackgrounded` twice (SDK race condition).

**Example:** `AppBackgrounded → AppBackgrounded`

**Frequency:** 948 occurrences

**Fix:** Same prev_state check.

---

### Edge Case 1.5 — terminal → terminal (double session end)

**What happens:** SDK fires `VideoSessionEnd` twice.

**Example:** `VideoSessionEnd → VideoSessionEnd` (avg 1.3 sec apart)

**Frequency:** 295 occurrences

**Fix:** First terminal is absorbing; second is discarded.

---

## Category 2: Terminal State Edge Cases

### Edge Case 2.1 — Events after VideoSessionEnd

**What happens:** Session officially ended but SDK still delivers queued events (BG, heartbeats).

**Example:**
```
VideoSessionEnd         → state: TERMINAL → emit -1 (if was active)
AppBackgrounded         → DISCARD (session is dead)
network-bandwidth       → DISCARD
```

**Frequency:** 220 sessions have AppBackgrounded after End; 275 have heartbeats after End

**Avg delay:** 1.7 sec (BG), up to 35 min (network events)

**Fix:** Once state = TERMINAL, ignore all subsequent events for that session_id.

---

### Edge Case 2.2 — VideoPlay after SessionEnd (session restart)

**What happens:** User closes video then quickly reopens same content. SDK reuses session_id.

**Example:** `VideoSessionEnd → [49 sec gap] → VideoPlay/Play`

**Frequency:** 17 sessions

**Fix:** Treat terminal as absorbing. The "new" Play is ignored.

**Alternative (more complex):** Detect Play-after-End as a new logical session. But at 17 cases out of 10,866 (0.16%), the accuracy loss from ignoring is negligible.

---

### Edge Case 2.3 — Events after VideoError

**What happens:** Error kills the session; subsequent heartbeats are orphaned.

**Frequency:** 292 out of 293 error sessions — zero recovery. Error = death.

**Fix:** Same as 2.1 — VideoError → TERMINAL → absorb everything after.

---

### Edge Case 2.4 — Pause/resume after terminal

**What happens:** Late heartbeat delivery after session already ended.

**Example:** `VideoSessionEnd → [8 sec] → VideoHeartbeat/pause`

**Frequency:** 6 events

**Fix:** Absorbed by terminal state. No delta emitted.

---

## Category 3: Session Lifecycle Anomalies

### Edge Case 3.1 — Duplicate VideoSessionStart

**What happens:** SDK fires Start twice (race condition in app initialization).

**Example:** `VideoSessionStart → VideoSessionStart → VideoPlay/Play`

**Frequency:** 13 sessions

**Fix:** Both map to state = INACTIVE. No delta emitted for either (session starts inactive). Idempotent.

---

### Edge Case 3.2 — Duplicate VideoPlay

**What happens:** SDK fires Play twice.

**Example:** `VideoPlay/Play → VideoPlay/Play`

**Frequency:** 16 sessions (includes 17 Play→resume = Play while already active)

**Fix:** First Play → ACTIVE (+1 delta). Second Play → ACTIVE (same state, no delta). Handled by prev_state check.

---

### Edge Case 3.3 — Zero-duration session

**What happens:** All events at the exact same millisecond. Session bounced instantly.

**Example:** `Start(t=0), Play(t=0), End(t=0)` — all same timestamp

**Frequency:** 12 sessions

**Fix:** State machine processes in order: INACTIVE → ACTIVE (+1) → TERMINAL (-1). Net = 0. Session contributes 0 minutes of active time. The +1 and -1 land in the same minute bucket, so net contribution to that minute's concurrency = 0. Correct.

---

### Edge Case 3.4 — Session with multiple content_ids

**What happens:** One session switches from content A to content B mid-stream.

**Frequency:** 1 session (24 events for content A, then 1 event for content B)

**Fix:** Use the content_id of the FIRST event (or the one with most events) as canonical. The single event for content B is likely a metadata error.

---

## Category 4: Foreground/Background Edge Cases

### Edge Case 4.1 — AppForegrounded without preceding AppBackgrounded

**What happens:** App launched directly into foreground (from notification or system restore). The first BG/FG event in the session is `AppForegrounded`.

**Example:**
```
VideoSessionStart   → INACTIVE
VideoPlay/Play      → ACTIVE (+1)
AppForegrounded     → NO CHANGE (still ACTIVE)
```

**Frequency:** 45 sessions start with FG as first BG/FG event

**Fix:** AppForegrounded is ALWAYS a no-op for state. It never changes state.

---

### Edge Case 4.2 — AppForegrounded after pause (no resume follows)

**What happens:** User paused, backgrounded, came back to foreground, but DIDN'T resume playback. They're looking at the paused screen.

**Example:**
```
VideoHeartbeat/pause    → INACTIVE (-1)
AppBackgrounded         → INACTIVE (no delta, already inactive)
AppForegrounded         → INACTIVE (NO CHANGE — critical!)
[user stares at paused screen]
VideoHeartbeat/resume   → ACTIVE (+1)  ← only THIS activates
```

**Frequency:** Common pattern in the data (thousands of sessions)

**Why critical:** If AppForegrounded emitted +1, you'd count users who returned to the app but are staring at a paused screen as "actively watching."

**Fix:** AppForegrounded → no state change. Only `resume` or `Play` re-activates.

---

### Edge Case 4.3 — AppBackgrounded when already paused

**What happens:** User already paused (inactive). Then they switch apps (BG). Both = inactive.

**Example:**
```
VideoHeartbeat/pause    → INACTIVE (-1)
AppBackgrounded         → INACTIVE (no delta — already counted)
```

**Frequency:** 11,265 occurrences

**Fix:** Duplicate inactive check (Category 1). No delta.

---

### Edge Case 4.4 — Unpaired background (user never returns)

**What happens:** User backgrounds the app and never comes back. Session has no matching `AppForegrounded`.

**Example:**
```
... → ACTIVE → AppBackgrounded → INACTIVE (-1) → [silence forever]
```

**Frequency:** 407 sessions (3.7% of all sessions)

**Fix:** The -1 delta was already emitted at AppBackgrounded. The 90-second timeout is not needed here because the session is already marked inactive. If the session was the LAST event, it simply stays inactive. No further action needed.

---

### Edge Case 4.5 — Double AppForegrounded

**What happens:** SDK fires Foregrounded twice in a row (no BG between them).

**Example:** `AppForegrounded → AppForegrounded`

**Frequency:** 45 occurrences

**Fix:** Both are no-ops (FG never changes state). Harmless.

---

## Category 5: Timeout / Heartbeat Gap Edge Cases

### Edge Case 5.1 — Session active but no heartbeat for >90 seconds

**What happens:** Session's last known state is ACTIVE, but no events arrive for over 90 seconds. The session is likely dead (app killed, network lost, device off).

**Example:**
```
VideoHeartbeat/resume   → ACTIVE (+1) at T=100
[... 90 seconds pass, no events ...]
→ Emit -1 at T=190 (timeout)
```

**Frequency:** Affects every session that ends without an explicit SessionEnd in the unseen day data.

**Fix:** Watermark job runs every 60 seconds. Any session with `last_state = 'active'` AND `last_event_timestamp < now() - 90 seconds` gets a -1 delta emitted at `last_event_ts + 90s`.

---

### Edge Case 5.2 — Session resumes AFTER timeout

**What happens:** Session was timed out (marked inactive), but then a new event arrives (network reconnect after brief outage).

**Example:**
```
[last event at T=100, timeout at T=190]
→ state: INACTIVE (timed out)
[T=250] VideoHeartbeat/resume arrives
→ state: ACTIVE (+1) — session is alive again!
```

**Frequency:** Rare but possible (especially on mobile networks)

**Fix:** State machine processes the new event normally. Since `prev_state = inactive` and `new_state = active`, it emits +1. The session was correctly counted as dead for those 60 seconds, then correctly counted as alive again. Self-healing.

---

### Edge Case 5.3 — Buffering lasts longer than timeout (>90s)

**What happens:** User is buffering (BufferStart fired, no BufferEnd yet). No other events for 95 seconds. Should we timeout?

**Key insight:** BufferStart/BufferEnd are `VideoHeartbeat` events. They prove the session is alive. Any heartbeat event (regardless of sub-type) resets the liveness clock.

**Example:**
```
VideoHeartbeat/BufferStart  → T=100 (session alive, clock resets)
[90 seconds pass]
→ Should NOT timeout! Buffer events are still heartbeats.
```

**Frequency:** P99 buffer duration = 67 seconds. A handful exceed 90s.

**Fix:** The timeout checks `last_event_timestamp`, NOT `last_state_change_timestamp`. ANY event (including BufferStart, network-activity, buffer-health) resets the clock. Only the ABSENCE of all events triggers timeout.

---

### Edge Case 5.4 — Multi-day gap (43-hour session)

**What happens:** User watches on July 24, backgrounds the app, app stays in memory for 2 days, user returns on July 26.

**Timeline:**
```
Jul 24 12:00 → AppBackgrounded → INACTIVE (-1)
[42 hours of silence]
Jul 26 06:00 → AppForegrounded → INACTIVE (no change, FG is no-op)
Jul 26 06:00 → VideoHeartbeat/resume → ACTIVE (+1)
```

**Frequency:** 1 session

**Fix:** Handled naturally. The session was correctly inactive for 42 hours (no events = timed out after 90s anyway). When resume arrives on Jul 26, the normal state machine re-activates it. No special handling needed.

---

## Category 6: User/Session Identity Edge Cases

### Edge Case 6.1 — Same session_id, 2 different user_ids (profile switch)

**What happens:** Two users share a session_id on the same device. User A watches, then User B takes over — but the session_id persists.

**Example:**
```
session_id=ABC, user_id=Alice, events from 10:56 to 11:24
session_id=ABC, user_id=Bob,   events from 11:00 to 11:24  (overlapping!)
```

**Frequency:** 120 sessions (69% on iPhone, 21% on Sony Android TV)

**Impact:**
- Session-level concurrency: counts as 1 session → correct (1 stream)
- User-level concurrency: should count as 2 users → undercounted if we dedup by session

**Fix:** For the hackathon, count at SESSION level (the problem asks for session concurrency). Document that user-level requires splitting shared sessions at user_id change boundaries.

---

### Edge Case 6.2 — One user with 301 simultaneous sessions (bot/venue)

**What happens:** A single user_id has up to 110 active sessions at once across 4 TV platforms.

**Frequency:** 1 user, 301 sessions, 19,479 events

**Impact:** Contributes 110/2,316 = 4.7% of peak foreground concurrency

**Fix:** Don't exclude — each session is a real video stream consuming real resources. For "viewer count" metrics, optionally cap at N sessions per user. Flag in analysis.

---

### Edge Case 6.3 — Same user on multiple platforms simultaneously

**What happens:** User watches on phone AND tablet at the same time (account sharing or multi-device use).

**Frequency:** 85 users (mostly Phone + Tablet combo)

**Fix:** Count each session independently. Each device = 1 concurrent stream. This is correct for capacity planning (each stream uses bandwidth). Not anomalous.

---

### Edge Case 6.4 — User-level vs session-level concurrency at peak

**What happens:** At peak, 2,219 sessions are active from 2,167 unique users. The 52-session difference comes from users with multiple streams.

**Fix:** Provide BOTH metrics:
- `fg_concurrent_sessions` = sessions (for capacity planning)
- `fg_concurrent_users` = unique users (for business/audience metrics)

---

## Category 7: Timestamp / Ordering / Out-of-Order Edge Cases

### Edge Case 7.1 — Same-millisecond ties (multiple state events at same ts)

**What happens:** Two state-changing events arrive at the exact same timestamp for the same session.

**Common patterns:**
- `AppBackgrounded` + `pause` at same ms → both = INACTIVE (no conflict)
- `AppForegrounded` + `resume` at same ms → FG is no-op, resume = ACTIVE (no conflict)
- `pause` + `pause` at same ms → duplicate (no conflict)

**Frequency:** 894 state-changing ties

**Fix:** Process events in a deterministic order when timestamps tie. Priority:
1. `VideoSessionStart` (initial state)
2. `VideoPlay` (activation)
3. `VideoHeartbeat/resume` (re-activation)
4. `VideoHeartbeat/pause` (deactivation)
5. `AppBackgrounded` (deactivation)
6. `AppForegrounded` (no-op, process last)
7. `VideoSessionEnd` / `VideoError` (terminal, process last to capture any active time)

Since both common tie patterns (BG+pause, FG+resume) agree on the resulting state, ordering doesn't actually matter in practice. But defensive ordering prevents future issues.

---

### Edge Case 7.2 — Same-ms tie with conflicting states (theoretical)

**What happens:** `resume` and `pause` at the exact same timestamp.

**Frequency:** 0 in current data. But could exist in unseen day.

**Fix:** Use priority ordering above. `resume` (position 3) processes before `pause` (position 4). Final state = INACTIVE. Conservative choice (don't overcount).

---

### Edge Case 7.3 — Out-of-Order Events (OOO)

#### Current Data: Zero OOO Events

**Verified:** 0 out-of-order event pairs exist in the entire 905K-event dataset. Every event within a session has a timestamp ≥ the previous event's timestamp. Ordering is perfectly monotonic.

#### Events After SessionEnd: NOT Out-of-Order

The 802 events arriving after `VideoSessionEnd` are NOT out-of-order — they have timestamps genuinely AFTER the end event:

| Event After End | Count | Median Delay | Root Cause |
|---|---|---|---|
| `AppBackgrounded` | 247 | **559 ms** | SDK race: app backgrounds ~5ms after End fires |
| `network-bandwidth` | 297 | **492 sec** (8 min) | Queued SDK measurement flushed late |
| `Seek/pause/resume` | 185 | **500–990 sec** | Likely from a new logical session using same session_id |
| `VideoPlay/Play` | 15 | **1.3 sec** | User re-opened same content immediately |
| `BufferStart/End` | 36 | **617 sec** | Orphaned buffer events from stale connection |

**Two distinct patterns:**

1. **Near-immediate (5ms–2 sec):** `AppBackgrounded` and `VideoPlay` fire within milliseconds of SessionEnd. These are SDK race conditions — the end event and the next event happen almost simultaneously. **Timestamp order is correct; they are just logically "post-terminal."**

2. **Long-delayed (8–35 min):** `Seek`, `pause`, `resume`, `network-bandwidth` fire minutes after End. These are either orphaned events from a stale network connection, or a new viewing session that the SDK erroneously tagged with the old session_id.

**Conclusion:** These are in-order late arrivals, not out-of-order delivery. Our terminal-absorbing rule handles them correctly.

#### Defensive Handling for Unseen Day: OOO May Exist

Although zero OOO events exist in training data, the unseen day data might have them if:
- Events come from multiple Kafka partitions (different partitions = no ordering guarantee)
- Network retransmits deliver old events alongside new ones
- SDK batches events and flushes them in non-timestamp order

**Defensive strategy for OOO events:**

```sql
-- ALWAYS sort by event_timestamp within each session BEFORE processing
-- This is cheap (session-level sort) and guarantees correctness

ORDER BY video_session_id, event_timestamp, 
    -- Tie-breaking priority for same-ms events:
    multiIf(
        event_type = 'VideoSessionStart', 1,
        event_type = 'VideoPlay', 2,
        event_type = 'VideoHeartbeat' AND event = 'resume', 3,
        event_type = 'VideoHeartbeat' AND event = 'pause', 4,
        event_type = 'AppBackgrounded', 5,
        event_type = 'AppForegrounded', 6,
        event_type = 'VideoSessionEnd', 7,
        event_type = 'VideoError', 8,
        9
    )
```

**Why we handle OOO even though we haven't seen it:**
- The unseen day is a "surprise" dataset from the same universe
- At production scale, OOO is common due to distributed ingestion
- The fix is cheap (just `ORDER BY` before windowing) and adds zero latency
- If OOO doesn't exist in unseen day, the sort is a no-op (already ordered = fast)
- If OOO does exist and we DON'T sort, the state machine will emit wrong deltas

**Impact of OOO if NOT handled:**
```
Correct order:   Play(t=1) → pause(t=5) → resume(t=8)
  State machine: ACTIVE(+1) → INACTIVE(-1) → ACTIVE(+1)   ✓

Out-of-order:    Play(t=1) → resume(t=8) → pause(t=5)  ← arrived OOO
  State machine: ACTIVE(+1) → ACTIVE(no-op) → INACTIVE(-1)
  Result: session shows as INACTIVE at t=5, should be ACTIVE until t=5 then INACTIVE until t=8 then ACTIVE
  → WRONG concurrency between t=5 and t=8
```

---

### Edge Case 7.4 — Late events arriving well after SessionEnd (up to 35 min)

**What happens:** Events arrive long after the session was finalized.

**Max observed delay:** 2,081 seconds (35 minutes) for `network-bandwidth` events after SessionEnd.

**Breakdown by delay:**

| Delay Range | Events | Dominant Type | Action |
|---|---|---|---|
| 5ms – 2 sec | ~260 | AppBackgrounded, VideoPlay | Terminal absorbs; OR treat Play as new session |
| 2 sec – 60 sec | ~30 | pause, resume | Terminal absorbs (orphaned heartbeats) |
| 1 min – 10 min | ~200 | network-bandwidth, Seek | Terminal absorbs (stale SDK queue) |
| 10 min – 35 min | ~310 | Seek, resume, BufferStart | Terminal absorbs (very stale) |

**Fix:**
- **Batch processing (hackathon):** Sort all events by timestamp first → process → terminal absorbs everything after End. No issue.
- **Streaming (unseen day):** Set watermark = 35 min after SessionEnd before considering a session fully finalized. Events arriving within the watermark window are checked against terminal state (absorbed). Events arriving after watermark are discarded.
- **Alternative (simpler):** Don't use a watermark. Just always check `if current_state == terminal → discard`. This works regardless of arrival delay.

---

### Edge Case 7.5 — Out-of-Order at Ingestion Level (Streaming Pipeline)

**What happens (hypothetical for unseen day):** Events for the same session arrive at the ClickHouse table in non-timestamp order because:
- Multiple INSERT batches landed in different order
- Different producers/partitions sent events at different speeds

**Impact on Materialized Views:** If an MV fires on INSERT and uses `lag()` to get previous state, it would see events in insertion order, not timestamp order. If insertion order ≠ timestamp order, the state transitions would be wrong.

**Fix — Two approaches:**

**Approach A: Batch Reprocessing (recommended for hackathon)**
```sql
-- Process ALL events for a session at once, sorted correctly
-- This guarantees correctness regardless of insertion order
INSERT INTO concurrency_deltas
SELECT ... FROM (
    SELECT *, lag(implied_state) OVER (
        PARTITION BY video_session_id 
        ORDER BY event_timestamp,  -- sort by event time, NOT insert time
            <tie_break_priority>
    ) AS prev_state
    FROM raw_events
    WHERE ...
)
WHERE delta != 0;
```

**Approach B: ReplacingMergeTree + Periodic Reconciliation (production)**
- Use `session_state` (ReplacingMergeTree) to track latest known state per session
- If an OOO event arrives, it may temporarily produce a wrong delta
- Periodic reconciliation job (every 5 min) recalculates correct state from sorted history
- Corrects any drift caused by OOO processing

**Our choice: Approach A** — for the hackathon, we process data in batch. We sort by `event_timestamp` before computing state transitions. This guarantees correctness even if the unseen day has OOO events.

---

## Category 8: Content / Dimension Edge Cases

### Edge Case 8.1 — Empty video_type (content not in metadata)

**What happens:** 3% of sessions (250) have content_ids that join to empty `video_type` in the content table.

**Fix:** Map to `'unknown'` in the serving table. Include in overall concurrency but exclude from video_type-specific filters unless user asks for "unknown."

---

### Edge Case 8.2 — Audio language variants (hin / HIN / hin-hindi)

**What happens:** Same language appears in 3+ formats due to inconsistent SDK reporting.

**Examples:** `hin`, `HIN`, `hin-hindi` all = Hindi. `eng`, `ENG`, `eng-english` all = English.

**Frequency:** 41 distinct raw values → ~15 logical languages

**Fix:** Normalize at ingestion time:
```sql
lower(splitByChar('-', audio_language)[1]) AS audio_language_normalized
```

Mapping: `hin/HIN/hin-hindi → hin`, `eng/ENG/eng-english → eng`, `unk/UNK → unknown`

---

### Edge Case 8.3 — Empty/null dimensions

**What happens:** Some events have empty `audio_language` (1,991 events), empty `subtitle_language` (2,006), or empty `player_version` (1,534).

**Fix:** Replace empty strings with `'unknown'` at ingestion. Never drop events due to missing dimensions — they still contribute to overall concurrency.

---

## The Complete State Machine (All Edge Cases Handled)

```python
def compute_state_and_delta(event, current_state):
    """
    Returns: (new_state, delta)
    delta: +1, -1, or 0 (no change)
    """
    
    # RULE 2: Terminal is absorbing — nothing escapes
    if current_state == 'terminal':
        return ('terminal', 0)
    
    # Determine what state this event implies
    if event_type == 'VideoPlay':
        implied_state = 'active'
    
    elif event_type == 'VideoHeartbeat' and event == 'resume':
        implied_state = 'active'
    
    elif event_type == 'AppBackgrounded':
        implied_state = 'inactive'
    
    elif event_type == 'VideoHeartbeat' and event == 'pause':
        implied_state = 'inactive'
    
    elif event_type == 'VideoSessionEnd':
        implied_state = 'terminal'
    
    elif event_type == 'VideoError':
        implied_state = 'terminal'
    
    elif event_type == 'VideoSessionStart':
        implied_state = 'inactive'  # not yet playing
    
    elif event_type == 'AppForegrounded':
        # RULE 3: FG alone does NOT activate
        implied_state = current_state  # NO CHANGE
    
    else:
        # All other events (heartbeat subtypes like buffer-health,
        # network-activity, video-resize, Seek, video_forward, etc.)
        # do NOT change state — but they DO reset the liveness clock
        implied_state = current_state  # NO CHANGE
    
    # RULE 1: Only emit delta when state actually changes
    if implied_state == current_state:
        delta = 0
    elif current_state != 'active' and implied_state == 'active':
        delta = +1   # session becomes active
    elif current_state == 'active' and implied_state in ('inactive', 'terminal'):
        delta = -1   # session becomes inactive/dead
    else:
        # inactive → terminal: no delta (wasn't counted anyway)
        delta = 0
    
    return (implied_state, delta)
```

---

## SQL Implementation (ClickHouse)

```sql
-- The state machine as a ClickHouse query
-- Uses lag() to compare previous state before emitting deltas

WITH state_events AS (
    SELECT 
        video_session_id,
        event_timestamp,
        event_type,
        event,
        platform,
        content_id,
        country,
        CASE
            WHEN event_type = 'VideoPlay' THEN 'active'
            WHEN event_type = 'VideoHeartbeat' AND event = 'resume' THEN 'active'
            WHEN event_type = 'AppBackgrounded' THEN 'inactive'
            WHEN event_type = 'VideoHeartbeat' AND event = 'pause' THEN 'inactive'
            WHEN event_type = 'VideoSessionEnd' THEN 'terminal'
            WHEN event_type = 'VideoError' THEN 'terminal'
            WHEN event_type = 'VideoSessionStart' THEN 'inactive'
            -- AppForegrounded and all other events: NULL (no state change)
            ELSE NULL
        END AS implied_state
    FROM raw_events
    WHERE event_type IN (
        'VideoPlay', 'AppBackgrounded', 'VideoSessionEnd', 
        'VideoError', 'VideoSessionStart'
    ) OR (event_type = 'VideoHeartbeat' AND event IN ('pause', 'resume'))
),

-- Filter only events that imply a state
filtered AS (
    SELECT * FROM state_events WHERE implied_state IS NOT NULL
),

-- Add previous state using lag()
with_prev AS (
    SELECT 
        *,
        lag(implied_state) OVER (
            PARTITION BY video_session_id 
            ORDER BY event_timestamp
        ) AS prev_state
    FROM filtered
),

-- Apply the 3 rules: only emit delta when state CHANGES
-- and terminal absorbs everything after
deltas AS (
    SELECT
        video_session_id,
        event_timestamp,
        platform,
        content_id,
        country,
        implied_state,
        prev_state,
        CASE
            -- Rule 2: if previous was terminal, skip (absorbed)
            WHEN prev_state = 'terminal' THEN 0
            -- Rule 1: no change = no delta
            WHEN implied_state = prev_state THEN 0
            WHEN implied_state = coalesce(prev_state, implied_state) THEN 0
            -- Becoming active
            WHEN implied_state = 'active' 
                 AND coalesce(prev_state, 'inactive') != 'active' THEN 1
            -- Becoming inactive or terminal from active
            WHEN coalesce(prev_state, 'inactive') = 'active' 
                 AND implied_state IN ('inactive', 'terminal') THEN -1
            -- inactive → terminal: no delta (wasn't counted)
            ELSE 0
        END AS delta
    FROM with_prev
)

-- Final output: only non-zero deltas, bucketed by minute
SELECT
    toStartOfMinute(fromUnixTimestamp64Milli(toInt64(event_timestamp))) AS minute,
    platform,
    country,
    content_id,
    sum(delta) AS session_delta
FROM deltas
WHERE delta != 0
GROUP BY minute, platform, country, content_id;
```

---

## Testing: How to Verify Each Edge Case

### Verification Query: Count duplicate transitions that SHOULD be filtered

```sql
-- This should return 0 if our state machine is correct
-- (no deltas emitted for same-state transitions)
SELECT count() AS leaked_duplicates
FROM deltas_table
WHERE (prev_state = 'active' AND implied_state = 'active' AND delta != 0)
   OR (prev_state = 'inactive' AND implied_state = 'inactive' AND delta != 0)
   OR (prev_state = 'terminal' AND delta != 0);
```

### Verification: Concurrency never goes negative

```sql
-- Running total should never be < 0
SELECT min(running_total) AS min_concurrency
FROM (
    SELECT sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    GROUP BY minute
);
-- Expected: 0 (not negative)
```

### Verification: Delta sum equals 0 for completed sessions

```sql
-- For any session that has a SessionEnd, total deltas should net to 0
SELECT video_session_id, sum(delta) AS net_delta
FROM deltas_table
WHERE video_session_id IN (
    SELECT video_session_id FROM raw_events 
    WHERE event_type = 'VideoSessionEnd'
)
GROUP BY video_session_id
HAVING net_delta != 0;
-- Expected: empty result (all closed sessions net to 0)
```

### Verification: Peak matches our analysis

```sql
-- Should return ~2,316 for July 26
SELECT max(running_total) AS peak_fg
FROM (
    SELECT sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE toDate(minute) = '2026-07-26'
    GROUP BY minute
);
```

---

## Priority Matrix

| Priority | Edge Cases | Handles | Implementation Effort |
|----------|-----------|---------|----------------------|
| **P0** (blocks correctness) | 1.1–1.5, 2.1–2.4, 4.1–4.2, 5.1 | Duplicates, terminal, FG≠active, timeout | `lag()` + CASE + watermark job |
| **P1** (2-5% accuracy) | 4.3–4.4, 5.2–5.3, 6.1, 7.1, 8.1–8.3 | Unpaired events, resume after timeout, dimensions | Normalization + edge logic |
| **P2** (<1% accuracy) | 3.1–3.4, 5.4, 6.2–6.4, 7.2–7.4 | Lifecycle dupes, multi-day, identity | Documentation + defensive code |

---

## Unseen Day: Edge Cases to Expect

Based on the current data patterns, the unseen day will likely have:

| Edge Case | Expected Frequency | Readiness |
|-----------|-------------------|-----------|
| Duplicate transitions | ~25,000+ | ✅ Handled by prev_state check |
| Open sessions (no SessionEnd) | 5-10% of sessions | ✅ Handled by 90s timeout |
| Late arrivals | ~2% of sessions | ✅ Handled by terminal absorbing |
| Resume-heavy sessions | ~11% of sessions | ✅ Handled by idempotent logic |
| 301-session bot user | Maybe | ✅ Doesn't break anything |
| New dimension values | Possible | ✅ LowCardinality handles new values |
| Higher event rate | Probable | ✅ Architecture scales linearly |
| Out-of-order events | Possible (not seen yet) | ⚠️ Sort by timestamp before processing |
| Sessions spanning midnight | Likely | ✅ Partition by date handles it |

---
