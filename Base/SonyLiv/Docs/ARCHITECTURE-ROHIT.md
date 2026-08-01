# Architecture & Implementation Plan
## Foreground-Only Concurrency at 100x Scale
### Token Burners — Click-a-thon 2026

---

## 1. Scale Context

| Metric | Current (Hackathon) | 100x Scale (Production) |
|--------|-------------------|------------------------|
| Raw events/day | 905K | **90.5M** |
| Sessions/day | 10.8K | **1.08M** |
| Users/day | 9.6K | **960K** |
| Content IDs | 3.3K | **330K** |
| Peak concurrent (FG) | 2.3K | **230K** |
| Events/second (peak) | ~7K/min = 117/sec | **~11,700/sec** |
| Storage (raw)/day | ~200MB | **~20GB** |
| Storage (raw)/year | — | **~7.3TB** |

---

## 2. Architecture Overview (3-Layer Model)

```
┌─────────────────────────────────────────────────────────────────┐
│                        QUERY LAYER                               │
│  Dashboard queries hit pre-aggregated serving tables             │
│  Sub-second latency, any dimension filter, any time grain        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ reads
┌───────────────────────────▼─────────────────────────────────────┐
│                    SERVING LAYER (Layer 3)                        │
│                                                                  │
│  ┌──────────────────────┐  ┌─────────────────────────────────┐  │
│  │ concurrency_minutes  │  │ session_active_intervals        │  │
│  │ (SummingMergeTree)   │  │ (ReplacingMergeTree)            │  │
│  │                      │  │                                 │  │
│  │ Per-minute delta     │  │ Per-session: active ranges,     │  │
│  │ counts by dimension  │  │ last state, dimensions          │  │
│  └──────────┬───────────┘  └──────────────┬──────────────────┘  │
│             │                              │                     │
└─────────────┼──────────────────────────────┼─────────────────────┘
              │ populated by MV              │ populated by MV
┌─────────────┼──────────────────────────────┼─────────────────────┐
│             │   PROCESSING LAYER (Layer 2) │                     │
│             │                              │                     │
│  ┌──────────▼──────────────────────────────▼──────────────────┐  │
│  │          Materialized Views (on INSERT)                    │  │
│  │                                                            │  │
│  │  MV1: State Machine → active intervals → +1/-1 deltas     │  │
│  │  MV2: Session state tracker (upsert last known state)      │  │
│  └──────────▲─────────────────────────────────────────────────┘  │
│             │                                                    │
└─────────────┼────────────────────────────────────────────────────┘
              │ triggers on insert
┌─────────────┼────────────────────────────────────────────────────┐
│             │        INGESTION LAYER (Layer 1)                    │
│  ┌──────────┴───────────────────────────────────────────────┐    │
│  │  raw_events (MergeTree)                                  │    │
│  │  + content_metadata (join table)                         │    │
│  │                                                          │    │
│  │  Events arrive → INSERT → MVs fire automatically         │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Why This Architecture

### Why NOT per-minute explosion?
At 100x scale: 1.08M sessions × avg 16 minutes = **17.3M rows per day** just for minute-explosion. With 10+ dimensions, that becomes 173M+ dimension-combinations. Unsustainable for real-time queries.

### Why delta model (+1/-1)?
- Each active interval generates exactly 2 rows (start delta, end delta)
- Cumulative sum reconstructs concurrency at any granularity
- Additive: `SummingMergeTree` auto-merges duplicate keys
- Incremental: new heartbeat → update end delta; no full rebuild
- Filter-friendly: store dimensions with each delta row

### Why 3 layers?
1. **Raw** = source of truth (immutable, append-only)
2. **Processing** = MVs do the heavy lifting on INSERT (zero query-time cost)
3. **Serving** = dashboard reads pre-computed data (sub-second)

---

## 4. Table Designs

### 4.1 Layer 1: Raw Events Table

```sql
CREATE TABLE raw_events (
    event_timestamp Int64,
    video_session_id String,
    user_id String,
    content_id Int64,
    event_type LowCardinality(String),
    event LowCardinality(String),
    platform LowCardinality(String),
    app_version LowCardinality(String),
    country LowCardinality(String),
    audio_language LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version LowCardinality(String),
    session_start_epoch Int64
) ENGINE = MergeTree()
PARTITION BY toDate(fromUnixTimestamp64Milli(event_timestamp))
ORDER BY (video_session_id, event_timestamp)
SETTINGS index_granularity = 8192;
```

**Key Design Decisions:**
- `PARTITION BY date` → efficient partition pruning for time-range queries
- `ORDER BY (video_session_id, event_timestamp)` → fast session-level scans
- `LowCardinality` on all low-cardinality dimensions → compression + speed
- No TTL at hackathon scale; at 100x add `TTL toDate(...) + INTERVAL 90 DAY`


### 4.2 Layer 1: Content Metadata (Dictionary)

```sql
CREATE TABLE content_metadata (
    content_id Int64,
    title String,
    video_type LowCardinality(String),
    category LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY content_id;

-- For fast lookups, also create a Dictionary:
CREATE DICTIONARY content_dict (
    content_id Int64,
    title String,
    video_type String,
    category String
) PRIMARY KEY content_id
SOURCE(CLICKHOUSE(TABLE 'content_metadata'))
LIFETIME(MIN 300 MAX 600)
LAYOUT(FLAT());
```

**Why Dictionary?** At 100x scale (330K content items), a flat dictionary fits in memory. JOINs at query time become `dictGet()` lookups — O(1) per row.

### 4.3 Layer 2: Session State Tracker (ReplacingMergeTree)

```sql
CREATE TABLE session_state (
    video_session_id String,
    user_id String,
    content_id Int64,
    platform LowCardinality(String),
    country LowCardinality(String),
    audio_language LowCardinality(String),
    app_version LowCardinality(String),
    
    -- State tracking
    last_event_timestamp Int64,
    last_state LowCardinality(String),  -- 'active', 'inactive', 'terminal'
    session_start_ts Int64,
    session_end_ts Int64,              -- 0 if still open
    total_active_ms Int64,
    total_inactive_ms Int64,
    event_count UInt32,
    
    -- Version for ReplacingMergeTree
    version UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY (video_session_id)
PARTITION BY toDate(fromUnixTimestamp64Milli(session_start_ts));
```

**Why ReplacingMergeTree?**
- Each heartbeat updates the session's state → INSERT new row with higher version
- Background merge keeps only the latest version per session
- Queries use `FINAL` or `argMax` to get current state
- At 100x: 1.08M rows (one per session per day) — trivial


### 4.4 Layer 3: Concurrency Deltas (SummingMergeTree) — THE KEY TABLE

```sql
CREATE TABLE concurrency_deltas (
    -- Time bucket
    minute DateTime,
    
    -- Dimensions (for filtering)
    platform LowCardinality(String),
    country LowCardinality(String),
    video_type LowCardinality(String),
    category LowCardinality(String),
    content_id Int64,
    
    -- Delta values (summed on merge)
    session_delta Int32,   -- +1 when active interval starts, -1 when it ends
    user_delta Int32       -- same but deduplicated per user (for unique viewers)
    
) ENGINE = SummingMergeTree((session_delta, user_delta))
PARTITION BY toDate(minute)
ORDER BY (minute, platform, video_type, country, category, content_id);
```

**Why SummingMergeTree?**
- Multiple deltas at the same (minute, dimension) combo → automatically summed during merge
- Net change per minute = what you need for cumulative sum
- At 100x scale: worst case = 1440 minutes × 10 platforms × 3 video_types × 80 categories = ~3.5M rows/day
- Actual rows much less (sparse: not all dimension combos active every minute)
- Queries scan tiny amounts of data

**Why this ORDER BY?**
- `minute` first → time-range queries prune efficiently
- `platform, video_type` next → most common dashboard filters
- `country, category, content_id` → less selective but still useful

### 4.5 Layer 3: Pre-Computed Minute Concurrency (for dashboard queries)

```sql
CREATE TABLE concurrency_per_minute (
    minute DateTime,
    platform LowCardinality(String),
    country LowCardinality(String),
    video_type LowCardinality(String),
    category LowCardinality(String),
    
    -- Pre-computed concurrency (filled by scheduled job)
    fg_concurrent_sessions UInt32,
    fg_concurrent_users UInt32,
    naive_concurrent_sessions UInt32
    
) ENGINE = ReplacingMergeTree()
PARTITION BY toDate(minute)
ORDER BY (minute, platform, video_type, country, category);
```

**This is the "dashboard table"** — queries hit this directly for sub-second response.
Populated by a periodic job (every 1–5 minutes) that:
1. Reads `concurrency_deltas`
2. Computes cumulative sum
3. INSERTs the absolute concurrency per minute


---

## 5. The State Machine (Processing Logic)

### 5.1 State Transition Rules

```
State: INACTIVE (initial)
  → VideoPlay/Play              → ACTIVE   (emit +1)
  → VideoHeartbeat/resume       → ACTIVE   (emit +1)
  → AppForegrounded (alone)     → stays INACTIVE (Rule 3: FG ≠ active)
  → VideoSessionEnd             → TERMINAL (no delta — wasn't counted)

State: ACTIVE
  → AppBackgrounded             → INACTIVE (emit -1)
  → VideoHeartbeat/pause        → INACTIVE (emit -1)
  → VideoSessionEnd             → TERMINAL (emit -1)
  → VideoError                  → TERMINAL (emit -1)
  → No event for 90 seconds     → INACTIVE (emit -1 at timeout)
  → VideoHeartbeat/resume       → stays ACTIVE (Rule 1: no duplicate delta)
  → VideoPlay/Play              → stays ACTIVE (Rule 1: no duplicate delta)
  → AppForegrounded             → stays ACTIVE (FG is always no-op)

State: INACTIVE
  → VideoHeartbeat/resume       → ACTIVE   (emit +1)
  → VideoPlay/Play              → ACTIVE   (emit +1)
  → AppBackgrounded             → stays INACTIVE (Rule 1: no duplicate delta)
  → VideoHeartbeat/pause        → stays INACTIVE (Rule 1: no duplicate delta)
  → AppForegrounded             → stays INACTIVE (Rule 3: FG ≠ active)
  → VideoSessionEnd             → TERMINAL (no delta — wasn't counted)

State: TERMINAL (absorbing — Rule 2)
  → ANY EVENT                   → stays TERMINAL (discard everything)
```

### 5.2 The 3 Non-Negotiable Rules (from Edge Case Analysis)

| Rule | What It Prevents | Occurrences in Data |
|------|-----------------|---------------------|
| **Rule 1:** Only emit delta when `prev_state != new_state` | 9,950 phantom +1s and 14,907 phantom -1s | 25,768 events |
| **Rule 2:** Terminal is absorbing (no escape) | Dead sessions resurrecting | 538 post-terminal events |
| **Rule 3:** AppForegrounded alone does NOT activate | Counting paused-screen viewers as active | Thousands of FG→pause sequences |

### 5.3 What Generates Deltas

Only ACTUAL state changes produce deltas:

| Transition | Delta | Condition |
|-----------|-------|-----------|
| INACTIVE → ACTIVE | `+1` at this minute | prev_state was inactive/null |
| ACTIVE → INACTIVE | `-1` at this minute | prev_state was active |
| ACTIVE → TERMINAL | `-1` at this minute | prev_state was active |
| INACTIVE → TERMINAL | `0` (no delta) | Wasn't being counted |
| SAME → SAME | `0` (no delta) | Rule 1: skip duplicates |
| TERMINAL → anything | `0` (discard) | Rule 2: absorbing |
| Timeout (no event 90s) | `-1` at last_event_minute + 2 | Only if last_state was active |

### 5.4 Out-of-Order Event Handling

**Current data:** Zero OOO events observed (all timestamps monotonically increase per session).

**Defensive design for unseen day:** Always sort events by timestamp before state machine processing.

```sql
-- Sort order for state machine input:
ORDER BY 
    video_session_id,
    event_timestamp,
    -- Tie-breaking for same-millisecond events:
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

**Why this tie-breaking order:**
- Start/Play first → establish the session and active state
- resume before pause → conservative (if tied, end up inactive = don't overcount)
- Terminal last → captures maximum active time before death

**If true OOO exists in unseen day:**
- The batch approach (sort all → process) handles it automatically
- The streaming approach needs reconciliation (see §6.3)

### 5.5 Handling Timeouts at Scale

**Problem:** If a session goes silent, we need to emit a `-1` delta 90 seconds after its last event. But materialized views only fire on INSERT — no INSERT means no trigger.

**Solution: Watermark-based cleanup job (runs every 60 seconds)**

```sql
-- Find sessions that timed out and emit -1 deltas
INSERT INTO concurrency_deltas (minute, platform, country, video_type, category, content_id, session_delta, user_delta)
SELECT 
    toStartOfMinute(fromUnixTimestamp64Milli(last_event_timestamp + 90000)) AS minute,
    platform,
    country,
    dictGet('content_dict', 'video_type', content_id) AS video_type,
    dictGet('content_dict', 'category', content_id) AS category,
    content_id,
    -1 AS session_delta,
    0 AS user_delta
FROM session_state FINAL
WHERE last_state = 'active'
    AND last_event_timestamp < (toUnixTimestamp64Milli(now()) - 90000)
    AND session_end_ts = 0;
```

**At 100x scale:** This scans only sessions in 'active' state with stale timestamps — typically <1% of total sessions at any moment (~10K rows max). Sub-second execution.

### 5.6 Liveness Clock (What Resets Timeout)

ANY event from the session resets the 90-second timeout, not just state-changing events:

| Event | Resets Clock? | Changes State? |
|-------|:---:|:---:|
| VideoHeartbeat/buffer-health | ✅ | ❌ |
| VideoHeartbeat/network-activity | ✅ | ❌ |
| VideoHeartbeat/video-resize | ✅ | ❌ |
| VideoHeartbeat/BufferStart | ✅ | ❌ |
| VideoHeartbeat/Seek | ✅ | ❌ |
| VideoHeartbeat/resume | ✅ | ✅ |
| VideoHeartbeat/pause | ✅ | ✅ |
| AppBackgrounded | ✅ | ✅ |
| AppForegrounded | ✅ | ❌ |

This means a session buffering for 2 minutes (with BufferStart/BufferEnd heartbeats) stays alive — the heartbeats prove the SDK is running.


---

## 6. Materialized View Logic

### 6.1 MV1: Event Enrichment + State Detection

```sql
CREATE MATERIALIZED VIEW mv_state_transitions
TO session_state
AS
SELECT
    video_session_id,
    user_id,
    content_id,
    platform,
    country,
    audio_language,
    app_version,
    event_timestamp AS last_event_timestamp,
    
    CASE
        WHEN event_type = 'VideoPlay' THEN 'active'
        WHEN event_type = 'VideoHeartbeat' AND event = 'resume' THEN 'active'
        WHEN event_type = 'AppBackgrounded' THEN 'inactive'
        WHEN event_type = 'VideoHeartbeat' AND event = 'pause' THEN 'inactive'
        WHEN event_type = 'VideoSessionEnd' THEN 'terminal'
        WHEN event_type = 'VideoError' THEN 'terminal'
        ELSE 'no_change'
    END AS last_state,
    
    session_start_epoch AS session_start_ts,
    if(event_type = 'VideoSessionEnd', event_timestamp, 0) AS session_end_ts,
    0 AS total_active_ms,
    0 AS total_inactive_ms,
    toUInt32(1) AS event_count,
    event_timestamp AS version
    
FROM raw_events
WHERE event_type IN ('VideoPlay', 'AppBackgrounded', 'VideoSessionEnd', 'VideoError', 'VideoSessionStart')
    OR (event_type = 'VideoHeartbeat' AND event IN ('pause', 'resume'));
```

### 6.2 MV2: Delta Generation (The Core)

This is the critical MV that produces +1/-1 deltas. The challenge is that a single event doesn't know the previous state — we need to compare with the session's last known state.

**Approach A: Batch delta computation (recommended for hackathon)**

Rather than a pure MV (which can't easily look up prior state), use a **scheduled INSERT** that runs every 30 seconds:

```sql
-- Scheduled job: compute new deltas from recent state changes
INSERT INTO concurrency_deltas
WITH recent_transitions AS (
    SELECT 
        video_session_id,
        last_event_timestamp,
        last_state,
        platform,
        country,
        dictGet('content_dict', 'video_type', content_id) AS video_type,
        dictGet('content_dict', 'category', content_id) AS category,
        content_id,
        lag(last_state) OVER (PARTITION BY video_session_id ORDER BY version) AS prev_state
    FROM session_state FINAL
    WHERE last_event_timestamp >= (toUnixTimestamp64Milli(now()) - 60000)  -- last 60 seconds
)
SELECT
    toStartOfMinute(fromUnixTimestamp64Milli(last_event_timestamp)) AS minute,
    platform,
    country,
    video_type,
    category,
    content_id,
    CASE
        WHEN prev_state != 'active' AND last_state = 'active' THEN 1
        WHEN prev_state = 'active' AND last_state IN ('inactive', 'terminal') THEN -1
        ELSE 0
    END AS session_delta,
    0 AS user_delta
FROM recent_transitions
WHERE session_delta != 0;
```


**Approach B: Pure MV with self-join (more complex but truly real-time)**

```sql
CREATE MATERIALIZED VIEW mv_concurrency_deltas
TO concurrency_deltas
AS
SELECT
    toStartOfMinute(fromUnixTimestamp64Milli(r.event_timestamp)) AS minute,
    r.platform,
    r.country,
    dictGet('content_dict', 'video_type', r.content_id) AS video_type,
    dictGet('content_dict', 'category', r.content_id) AS category,
    r.content_id,
    
    -- Compute delta based on state transition
    multiIf(
        -- Becoming active
        r.event_type = 'VideoPlay', 1,
        r.event_type = 'VideoHeartbeat' AND r.event = 'resume', 1,
        -- Becoming inactive
        r.event_type = 'AppBackgrounded', -1,
        r.event_type = 'VideoHeartbeat' AND r.event = 'pause', -1,
        r.event_type = 'VideoSessionEnd', -1,
        r.event_type = 'VideoError', -1,
        0
    ) AS session_delta,
    
    0 AS user_delta
    
FROM raw_events r
WHERE (r.event_type IN ('VideoPlay', 'AppBackgrounded', 'VideoSessionEnd', 'VideoError')
    OR (r.event_type = 'VideoHeartbeat' AND r.event IN ('pause', 'resume')));
```

**IMPORTANT:** This approach over-counts! If a session is already inactive and gets another `pause`, it emits an extra `-1`. We handle this via a correction step (see §6.3).

### 6.3 Correction for Double Transitions

The simple MV in Approach B doesn't track prior state. To fix:

1. **Insert-time filter**: Only emit deltas for actual state CHANGES
2. **Periodic reconciliation**: Every 5 minutes, compare `concurrency_deltas` sum vs actual active sessions from `session_state`

```sql
-- Reconciliation: compute correction delta
INSERT INTO concurrency_deltas
SELECT
    toStartOfMinute(now()) AS minute,
    platform, country, video_type, category, content_id,
    (actual_active - computed_active) AS session_delta,
    0 AS user_delta
FROM (
    SELECT platform, country, video_type, category, content_id,
        count() AS actual_active
    FROM session_state FINAL
    WHERE last_state = 'active'
    GROUP BY platform, country, video_type, category, content_id
) actual
FULL OUTER JOIN (
    SELECT platform, country, video_type, category, content_id,
        sum(session_delta) AS computed_active
    FROM concurrency_deltas
    GROUP BY platform, country, video_type, category, content_id
) computed USING (platform, country, video_type, category, content_id)
WHERE actual_active != computed_active;
```


---

## 7. Query Patterns (Dashboard Layer)

### 7.1 Peak Concurrency for Time Range + Filter

```sql
-- "What was peak foreground concurrency on ANDROID_PHONE between 10:00-11:00?"
SELECT max(running_total) AS peak_concurrency
FROM (
    SELECT 
        minute,
        sum(session_delta) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE minute BETWEEN '2026-07-26 10:00:00' AND '2026-07-26 11:00:00'
        AND platform = 'ANDROID_PHONE'
)
```

**Query complexity at 100x:**
- Partition pruning: 1 day
- Filter on platform: 10% of data
- Minutes in range: 60
- Rows scanned: ~60 × 80 categories × 1 platform = ~4,800 rows
- **Latency: <10ms**

### 7.2 Average Concurrency for Time Range

```sql
-- "Average foreground concurrency for Live content today?"
SELECT round(avg(running_total)) AS avg_concurrency
FROM (
    SELECT 
        minute,
        sum(session_delta) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE toDate(minute) = '2026-07-26'
        AND video_type = 'live'
)
```

### 7.3 Concurrency Curve (Minute-by-Minute)

```sql
-- "Show me the concurrency curve for the last 2 hours"
SELECT 
    minute,
    sum(sum(session_delta)) OVER (ORDER BY minute) AS concurrent_sessions
FROM concurrency_deltas
WHERE minute >= now() - INTERVAL 2 HOUR
GROUP BY minute
ORDER BY minute
```

### 7.4 Multi-Dimension Peak (Different Peaks per Dimension)

```sql
-- "Peak concurrency per platform in the last hour"
SELECT 
    platform,
    max(running_total) AS peak,
    argMax(minute, running_total) AS peak_minute
FROM (
    SELECT 
        platform,
        minute,
        sum(session_delta) OVER (PARTITION BY platform ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE minute >= now() - INTERVAL 1 HOUR
)
GROUP BY platform
ORDER BY peak DESC
```

### 7.5 From Pre-Computed Table (Fastest)

```sql
-- If using concurrency_per_minute table:
SELECT 
    max(fg_concurrent_sessions) AS peak,
    argMax(minute, fg_concurrent_sessions) AS peak_minute
FROM concurrency_per_minute
WHERE toDate(minute) = '2026-07-26'
    AND platform = 'ANDROID_PHONE'
```
**Latency: <5ms** (direct index scan, no window function)


---

## 8. Handling Open Sessions & Late Arrivals

### 8.1 Open Sessions (No SessionEnd Yet)

**Problem:** A session has `last_state = 'active'` but no end event. If no heartbeat arrives for 90 seconds, it's dead.

**Solution:** Watermark job (runs every 60 seconds):

```sql
-- Mark timed-out sessions as inactive + emit -1 delta
INSERT INTO concurrency_deltas
SELECT
    toStartOfMinute(fromUnixTimestamp64Milli(s.last_event_timestamp + 90000)),
    s.platform, s.country,
    dictGet('content_dict', 'video_type', s.content_id),
    dictGet('content_dict', 'category', s.content_id),
    s.content_id,
    -1 AS session_delta,
    0 AS user_delta
FROM session_state s FINAL
WHERE s.last_state = 'active'
    AND s.last_event_timestamp < toUnixTimestamp64Milli(now()) - 90000
    AND s.session_end_ts = 0;

-- Also update session_state to mark them inactive
INSERT INTO session_state
SELECT 
    video_session_id, user_id, content_id, platform, country,
    audio_language, app_version,
    last_event_timestamp,
    'inactive' AS last_state,
    session_start_ts, 0 AS session_end_ts,
    total_active_ms, total_inactive_ms, event_count,
    last_event_timestamp + 90000 AS version  -- higher version
FROM session_state FINAL
WHERE last_state = 'active'
    AND last_event_timestamp < toUnixTimestamp64Milli(now()) - 90000
    AND session_end_ts = 0;
```

### 8.2 Late Arrivals (Events After SessionEnd)

**Problem:** 2.2% of sessions have events arriving after SessionEnd (up to 35 min late).

**Solution:**
1. Don't finalize sessions until `watermark_delay = 35 minutes` after SessionEnd
2. If a late `resume` arrives → re-emit `+1` delta (state machine handles it naturally)
3. The `session_state` table uses `ReplacingMergeTree(version)` — late events with higher timestamps simply create new versions

### 8.3 Unseen Day: Sessions Without End

The unseen day dataset may have sessions that never close. Our system handles this:
1. Session emits heartbeats → `session_state` keeps updating
2. 90-second timeout → watermark job marks them inactive
3. If heartbeat resumes → MV emits `+1` again
4. Final reconciliation after unseen day ingestion completes

---

## 9. Scale Analysis: 100x Behavior

### 9.1 Write Path

| Component | Current | 100x | Bottleneck? |
|-----------|---------|------|-------------|
| Raw inserts | 117/sec | 11,700/sec | ❌ ClickHouse handles 1M+ inserts/sec |
| MV processing | 117 events/sec → ~20 state changes/sec | 2,000 state changes/sec | ❌ Trivial |
| Delta generation | ~20 deltas/sec | 2,000 deltas/sec | ❌ Well within limits |
| Session state updates | ~117/sec | 11,700/sec | ⚠️ ReplacingMergeTree handles this, but merge pressure increases |
| Watermark timeout job | Scans ~100 active sessions | Scans ~10K active sessions | ❌ Sub-second |

### 9.2 Storage

| Table | Current/Day | 100x/Day | 100x/Year |
|-------|-------------|----------|-----------|
| raw_events | ~200MB | 20GB | 7.3TB |
| session_state | ~1MB | 100MB | 36GB |
| concurrency_deltas | ~0.5MB | 50MB | 18GB |
| concurrency_per_minute | ~0.1MB | 10MB | 3.6GB |

**Total at 100x:** ~7.4TB/year raw + ~58GB serving = **very manageable** for ClickHouse Cloud.

### 9.3 Query Path

| Query Type | Rows Scanned (100x) | Expected Latency |
|-----------|---------------------|-----------------|
| Peak concurrency (1 hour, 1 platform) | ~4,800 | **<10ms** |
| Peak concurrency (1 day, no filter) | ~120K | **<50ms** |
| Concurrency curve (1 hour) | ~60 | **<5ms** |
| Multi-dimension peak (all platforms, 1 hour) | ~48K | **<30ms** |
| Full day, all dimensions | ~1.2M | **<200ms** |

### 9.4 Scaling Plan: What Breaks at Each Level

#### At 10x (9M events/day, 100K sessions)
- **Nothing breaks.** All tables, MVs, and queries perform identically.
- Storage grows to ~2GB/day raw → trivial.

#### At 100x (90M events/day, 1M sessions)
- **Raw table:** 20GB/day. Add `TTL toDate(...) + INTERVAL 90 DAY` for auto-cleanup.
- **Session state merges:** 1M versions/day per session_id. Increase `merge_max_block_size`. Partition by day so merges are scoped.
- **Batch inserts:** Switch from row-by-row to batch INSERT (10K rows per batch) to reduce part creation.
- **OOO handling:** At this scale with distributed producers, OOO becomes likely. Batch sort approach still works (sort happens per-partition).

#### At 1000x (900M events/day, 10M sessions)
| Concern | Mitigation |
|---------|-----------|
| Raw table too large (200GB/day) | Tiered storage: hot (7 days SSD) → cold (S3/GCS) |
| MV lag on inserts | Batch inserts mandatory (50K+ rows per INSERT). Consider async MV processing. |
| Session state table size (10M rows/day) | Partition by hour (not day). Aggressive merge schedule. |
| Concurrency deltas cardinality | Drop `content_id` from delta table (move to separate content-level table). Keep only platform + video_type + country. |
| Window function at query time | Pre-compute cumulative sums in `concurrency_per_minute` table; queries never need window functions. |
| Peak concurrent sessions (230K+ at 100x) | No issue for delta model — storage doesn't grow with concurrent count, only with state changes. |

#### At 10,000x (9B events/day, 100M sessions) — Theoretical
| Concern | Mitigation |
|---------|-----------|
| Single-node capacity | Shard by `video_session_id` across multiple nodes. Each shard processes its sessions independently. |
| Cross-shard concurrency | Each shard emits local deltas → merge deltas at query time (additive: sum of sums = total sum). |
| State machine memory | Move from batch query to streaming processor (Flink/Kafka Streams) feeding ClickHouse as sink. |
| Query latency degradation | Add read replicas for query serving. Separate write path from read path. |

### 9.5 Why the Delta Model Scales Linearly

The key insight: **storage and compute grow with STATE CHANGES, not with events or sessions.**

| Scale | Events/day | State Changes/day | Delta Rows/day | Growth Factor |
|-------|-----------|-------------------|----------------|---------------|
| 1x | 905K | ~63K | ~63K | baseline |
| 100x | 90.5M | ~6.3M | ~6.3M | 100x (linear) |
| 1000x | 905M | ~63M | ~63M | 1000x (linear) |

Compare to per-minute explosion:
| Scale | Events/day | Minute-Rows/day | Growth Factor |
|-------|-----------|-----------------|---------------|
| 1x | 905K | ~174K | baseline |
| 100x | 90.5M | **17.3M** | 100x |
| 1000x | 905M | **173M** | 1000x but each row is wider |

Both grow linearly, but delta rows are **tiny** (1 int32 per row) while minute-rows need all dimension columns repeated. The delta model has ~10x better compression.

### 9.6 Batch Insert Strategy (Critical for Scale)

At scale, row-by-row inserts kill ClickHouse (too many small parts → merge storms).

**Strategy:**

```
Events arrive → Buffer in memory/Kafka (1-10 seconds) 
             → Batch INSERT (10K-50K rows per INSERT)
             → MV fires on the batch
             → Delta computation happens in batch
```

| Insert Size | Parts Created | Merge Pressure | Recommended Scale |
|-------------|--------------|----------------|-------------------|
| 1 row | 1 part per insert | 🔴 Catastrophic | Never in production |
| 100 rows | 1 part per 100 | 🟡 High | Dev/testing only |
| 10,000 rows | 1 part per 10K | 🟢 Normal | 100x scale |
| 50,000 rows | 1 part per 50K | 🟢 Optimal | 1000x scale |

For the hackathon: single bulk INSERT of all CSV data. No issue.
For unseen day: batch the streaming data into 10K-row inserts.

### 9.7 OOO at Scale

| Scale | OOO Likelihood | Handling |
|-------|---------------|----------|
| Hackathon (batch) | Zero (we load all data at once and sort) | Sort by timestamp in query |
| 100x (streaming) | Low (single Kafka partition per session) | Sort within batch before processing |
| 1000x (distributed) | Moderate (multiple producers, network delays) | Session-level sorting + reconciliation |
| 10,000x (geo-distributed) | High (cross-region replication lag) | Streaming processor with per-session state + late-arrival watermark |

**Our design handles all levels:** The batch approach (sort all events by session+timestamp before computing deltas) is correct regardless of insertion order. At higher scale, you'd move the sort to the streaming processor before ClickHouse.


---

## 10. Implementation Plan (Ordered Steps)

### Phase 1: Foundation (Tables + Data Load)
1. Create `raw_events` table with proper schema
2. Create `content_metadata` table + dictionary
3. Load CSV data into both tables
4. Verify row counts and basic queries

### Phase 2: State Machine (Active Interval Extraction)
5. Create `session_state` (ReplacingMergeTree)
6. Build MV that populates `session_state` from raw events
7. Backfill `session_state` from existing raw data
8. Verify: sample sessions match manual state analysis

### Phase 3: Delta Generation (Concurrency Computation)
9. Create `concurrency_deltas` (SummingMergeTree)
10. Build MV/scheduled job that generates deltas from state transitions
11. Backfill deltas for existing data
12. Verify: cumulative sum matches our analysis (peak = 2,316)

### Phase 4: Serving Layer (Dashboard Queries)
13. Create `concurrency_per_minute` table
14. Build scheduled job to populate it from deltas
15. Write benchmark queries and measure latency
16. Verify against ground truth

### Phase 5: Incremental Updates (Open Sessions)
17. Build watermark timeout job
18. Build late-arrival handler
19. Test with simulated streaming inserts
20. Measure end-to-end latency (event → queryable)

### Phase 6: Integration (ClickStack / LibreChat)
21. Instrument pipeline with ClickStack (ingestion lag, query perf)
22. OR: Build LibreChat interface with ClickHouse MCP server
23. Demo: "What's peak concurrency on Android in the last hour?"

### Phase 7: Unseen Day
24. Load unseen day data through pipeline
25. Run benchmark queries
26. Capture query logs/latencies as evidence
27. Submit results

---

## 11. Correctness Guarantees

### 11.1 The Correctness Contract

For any time window `[T1, T2]` and any dimension filter `F`:
```
Peak FG Concurrency = max over all minutes M in [T1,T2] of:
  (sum of session_delta WHERE minute <= M AND filter matches F)
```

This is equivalent to:
```
Count of sessions that have at least one ACTIVE interval overlapping minute M,
where ACTIVE is defined by the state machine in §5.1.
```

### 11.2 Known Edge Cases & Handling

| # | Edge Case | Frequency | Handling | Rule |
|---|-----------|-----------|----------|------|
| 1 | active→active (resume while playing) | 9,950 events | Skip — no delta | Rule 1 |
| 2 | inactive→inactive (pause then BG) | 14,907 events | Skip — no delta | Rule 1 |
| 3 | Events after SessionEnd/Error | 802 events (239 sessions) | Discard — terminal absorbs | Rule 2 |
| 4 | AppForegrounded without resume | Thousands | No state change — FG is always no-op | Rule 3 |
| 5 | Duplicate SessionStart/Play/End | 13-16 sessions | Idempotent (same implied state) | Rule 1 |
| 6 | 120 shared sessions (2 users) | 120 sessions | Count at session level | By design |
| 7 | 301-session bot user | 1 user | Count all sessions individually | By design |
| 8 | Zero-duration sessions | 12 sessions | +1 and -1 in same minute = net 0 | Natural |
| 9 | 43-hour session (multi-day gap) | 1 session | Timeout at +90s, re-activate on resume | Rule 1 + timeout |
| 10 | Mismatched BG/FG counts (407 unpaired) | 466 sessions | Already inactive; timeout handles | Timeout job |
| 11 | Same-ms timestamp ties | 894 events | Deterministic tie-breaking order | Sort order |
| 12 | OOO events (0 in data, defensive) | 0 observed | Sort by event_timestamp before processing | Defensive sort |
| 13 | Late arrivals (up to 35 min after End) | 802 events | Terminal state absorbs; no re-activation | Rule 2 |
| 14 | Buffering >90s (long buffer) | Handful | Any heartbeat resets liveness clock | Liveness design |
| 15 | Empty/null dimensions | ~2,000 events | Map to 'unknown', never drop | Normalization |
| 16 | Audio language variants (hin/HIN/hin-hindi) | 41 variants | Normalize at ingestion | Normalization |
| 17 | Sessions resuming after timeout | Rare | Normal state machine: inactive→active = +1 | Self-healing |
| 18 | Content_id switch mid-session | 1 session | Use first content_id as canonical | By design |

### 11.3 Validation Query (Sanity Check)

```sql
-- This should match our analysis: peak FG = ~2,316
SELECT max(running_total) AS peak_fg
FROM (
    SELECT minute,
        sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE toDate(minute) = '2026-07-26'
    GROUP BY minute
)
```


---

## 12. Trade-Off Analysis (For Judges)

### 12.1 Why Delta Model Over Alternatives

| Approach | Pros | Cons | Our Assessment |
|----------|------|------|---------------|
| **Per-minute explosion** | Simple to query | 17M rows/day at 100x; N×1440 rows per session | ❌ Doesn't scale |
| **Interval arrays per session** | Compact storage | Window functions on query; hard to filter by dimension | ❌ Query-heavy |
| **Delta model (+1/-1)** | Tiny storage, cumsum = O(N), incremental updates, dimension-friendly | Need window function at query time | ✅ **Chosen** |
| **Pre-computed per-minute** | Zero query-time computation | Stale for open sessions; N×dimensions rows | ✅ As supplementary cache |

### 12.2 Why SummingMergeTree Over AggregatingMergeTree

- `SummingMergeTree` automatically sums numeric columns on merge → perfect for deltas
- `AggregatingMergeTree` is for complex aggregates (uniq, quantiles) — overkill for simple +1/-1
- Simpler schema, simpler queries, lower overhead

### 12.3 Why ReplacingMergeTree for Session State

- Sessions evolve over time (new heartbeats update state)
- Need "latest state" per session without scanning all versions
- `FINAL` gives consistent reads; background merges handle cleanup
- Alternative (mutations) would require DELETE + INSERT = expensive at scale

### 12.4 Why Dictionary for Content Metadata

- 33K entries (330K at 100x) — fits in memory
- `dictGet()` = O(1) lookup vs JOIN = hash table build
- Content metadata changes rarely (new content added daily, not per-second)
- Saves materializing `video_type` and `category` in every MV

---

## 13. Latency Budget

| Component | Target Latency | How Achieved |
|-----------|---------------|--------------|
| Event → raw table | <1 second | Direct INSERT (ClickHouse native) |
| Raw → session_state | <1 second | MV triggers on INSERT |
| Session_state → delta | <30 seconds | Scheduled batch job (or MV approach B) |
| Delta → queryable concurrency | 0 (cumsum at query time) | Window function on pre-filtered data |
| Dashboard query response | **<50ms** | SummingMergeTree + ORDER BY key alignment |
| Pre-computed table query | **<5ms** | Direct index scan |

**End-to-end: Event occurs → reflected in dashboard: <35 seconds**

---

## 14. Monitoring with ClickStack (Integration Requirement)

### What to Monitor

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Ingestion lag | `system.parts` age vs wall clock | >60 seconds |
| MV processing backlog | `system.mutations` queue | >1000 pending |
| Query latency (P99) | `system.query_log` | >500ms |
| Concurrency anomaly | Delta in concurrency_deltas | >30% drop in 5 min |
| Session timeout rate | Watermark job output | >5% of active sessions |

### ClickStack Integration

```sql
-- Track query performance
SELECT 
    query_kind,
    quantile(0.99)(query_duration_ms) AS p99_latency,
    count() AS query_count,
    sum(read_rows) AS total_rows_read
FROM system.query_log
WHERE event_date = today()
    AND type = 'QueryFinish'
    AND query LIKE '%concurrency%'
GROUP BY query_kind;
```

### Optional: Concurrency Decline Alerting (LLM Use Case)

Monitor for sudden drops in concurrency that might indicate:
- Asset ended (expected)
- System issue (CDN failure, app crash)
- Content not engaging (business signal)

```sql
-- Detect >20% drop in 5-minute window
SELECT 
    minute,
    running_total,
    lagInFrame(running_total, 5) OVER (ORDER BY minute) AS five_min_ago,
    if(five_min_ago > 0, (five_min_ago - running_total) / five_min_ago, 0) AS drop_pct
FROM (
    SELECT minute, sum(sum(session_delta)) OVER (ORDER BY minute) AS running_total
    FROM concurrency_deltas
    WHERE minute >= now() - INTERVAL 30 MINUTE
    GROUP BY minute
)
WHERE drop_pct > 0.2
```

---

## 15. Implementation Priority for Hackathon

Given limited time, prioritize:

1. **P0 — Must have:**
   - Raw table + content dictionary
   - Delta computation (can be batch, not real-time MV)
   - Serving query that produces correct peak/avg concurrency
   - Benchmark query answers

2. **P1 — Should have:**
   - Materialized view for real-time delta generation
   - Session state tracker
   - Watermark timeout job
   - ClickStack or LibreChat integration

3. **P2 — Nice to have:**
   - Pre-computed minute table
   - Anomaly detection
   - Full streaming demo with replay

**The simplest correct implementation:**
1. Load data → raw table
2. Run a single big query that computes all active intervals + deltas → INSERT into serving table
3. Benchmark queries read serving table
4. For unseen day: repeat step 2 with new data

This is correct and fast enough. Incremental/streaming is the "great" version.

---

## 16. Key Numbers to Remember

| Fact | Value | Impact |
|------|-------|--------|
| FG/Naive ratio at peak | 76–81% | Foreground filtering matters |
| FG/Naive ratio average | 63% | 37% overcounting without state machine |
| Heartbeat interval | 30–40 sec | Timeout = 90 sec (2.25x) |
| Peak FG concurrent | 2,316 sessions | Ground truth anchor |
| Event window | 10:30–11:30 UTC | 60-minute live event |
| Platforms peak at different minutes | 10:43 to 11:03 | Can't pre-aggregate across dimensions |
| Sessions with late events | 2.2% (239 sessions) | Terminal absorbs — no watermark needed |
| Out-of-order events | **0 observed** | Sort defensively for unseen day |
| Duplicate transitions to filter | 25,768 events | Rule 1 prevents 25K wrong deltas |
| Post-terminal events to discard | 802 events | Rule 2 prevents dead sessions reviving |
| Median pause duration | 7.3 sec | Short pauses are common |
| Median BG duration | 35 sec | Significant — must exclude |
| All sessions have BG+pause | 90% | Core problem, not edge case |
| Max late arrival after End | 35 minutes | Terminal state handles it (no special watermark) |
| Sessions with same-ms ties | 894 state events | Tie-breaking sort order handles |
| Shared session_ids (2 users) | 120 sessions | Count at session level |
| 301-session anomaly user | 4.7% of peak | Flag but don't exclude |

---

## 17. Document Cross-References

| Document | Contains | Location |
|----------|----------|----------|
| DATA_ANALYSIS-ROHIT.md | Full dataset analysis, all metrics, state machine definition | `Docs/` |
| EDGE_CASES.md | 33 edge cases, complete state machine code, verification queries | `Docs/` |
| ARCHITECTURE-ROHIT.md | This document — tables, MVs, scaling, implementation | `Docs/` |
| PROBLEM_STATEMENT.md | Original requirements and evaluation criteria | `Base/SonyLiv/` |
| dataset_details.md | Column definitions and data dictionary | `Base/SonyLiv/` |

