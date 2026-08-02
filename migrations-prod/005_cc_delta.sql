-- Migration 005: Concurrency deltas fact table + recompute MV
--
-- Stores per-session +1/-1 deltas. Content-derived dimensions (video_type,
-- category, show_name) are NOT stored here — look them up via dict_content
-- at query time using content_id. Keeps the table lean and schema-change-proof.

CREATE TABLE IF NOT EXISTS fact_concurrency_deltas
(
    video_session_id String,
    user_id          String,
    minute           DateTime,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       UInt64,
    delta_sessions   Int8,           -- +1/-1 for active (fg AND playing AND fresh)
    delta_open       Int8,           -- +1/-1 for open (between START and END/timeout)
    computed_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toDate(minute)
ORDER BY (minute, video_session_id)
TTL toDate(minute) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;


CREATE MATERIALIZED VIEW IF NOT EXISTS mv_compute_concurrency
REFRESH EVERY 30 SECOND APPEND
TO fact_concurrency_deltas
AS
WITH
-- Step 1: Which sessions changed?
changed AS (
    SELECT DISTINCT video_session_id
    FROM raw_sessions_pending
),

-- Step 2: Full history for dirty sessions
deduped AS (
    SELECT DISTINCT
        video_session_id, user_id, event_ts, event_type, event,
        platform, country, content_id, video_resolution
    FROM fact_events
    WHERE video_session_id IN (SELECT video_session_id FROM changed)
),

-- Step 3: Classify to 9-signal alphabet
classified AS (
    SELECT video_session_id, user_id, platform, country, content_id, video_resolution, event_ts,
        CASE
            WHEN event_type = 'VideoSessionStart' THEN 'START'
            WHEN event_type = 'VideoSessionEnd'   THEN 'END'
            WHEN event_type = 'VideoPlay'         THEN 'PLAY'
            WHEN event_type = 'AppBackgrounded'   THEN 'BG'
            WHEN event_type = 'AppForegrounded'   THEN 'FG'
            WHEN event_type = 'VideoError'        THEN 'ERR'
            WHEN event_type = 'VideoHeartbeat' AND event IN ('pause','speed-pause','AdPause')    THEN 'PAUSE'
            WHEN event_type = 'VideoHeartbeat' AND event IN ('resume','speed-resume','AdResume') THEN 'RESUME'
            ELSE 'HB'
        END AS signal,
        CASE
            WHEN event_type = 'VideoSessionStart' THEN 1
            WHEN event_type = 'VideoPlay'         THEN 2
            WHEN event_type = 'AppForegrounded'   THEN 3
            WHEN event_type = 'VideoHeartbeat' AND event IN ('resume','speed-resume','AdResume') THEN 4
            WHEN event_type = 'VideoHeartbeat' AND event IN ('pause','speed-pause','AdPause')    THEN 5
            WHEN event_type = 'AppBackgrounded'   THEN 6
            WHEN event_type = 'VideoError'        THEN 7
            WHEN event_type = 'VideoSessionEnd'   THEN 8
            ELSE 9
        END AS tie_break
    FROM deduped
),

-- Step 4: Collapse to one sorted array per session
sorted AS (
    SELECT
        video_session_id,
        any(user_id) AS user_id,
        any(platform) AS platform,
        any(country) AS country,
        any(content_id) AS content_id,
        any(video_resolution) AS video_resolution,
        arraySort(groupArray((event_ts, tie_break, signal))) AS ev,
        min(event_ts) AS session_first_event,
        max(event_ts) AS session_last_event
    FROM classified
    GROUP BY video_session_id
),

-- Step 5: Three independent gates
gates AS (
    SELECT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event,
        arrayMap(x -> x.1, ev) AS ts_arr,
        arrayMap(x -> if(x=-1,1,x), arrayFill(x -> x>=0,
            arrayMap(s -> multiIf(s='FG',1,s='BG',0,-1), arrayMap(y->y.3, ev)))) AS fg,
        arrayMap(x -> if(x=-1,0,x), arrayFill(x -> x>=0,
            arrayMap(s -> multiIf(s IN ('PLAY','RESUME'),1,s='PAUSE',0,-1), arrayMap(y->y.3, ev)))) AS playing,
        arrayCumSum(arrayMap(s -> if(s='END',1,0), arrayMap(y->y.3, ev))) AS ended
    FROM sorted
),

-- Step 6: Segment + liveness cap + active flag
segmented AS (
    SELECT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event, ts_arr,
        arrayMap(i -> least(
            if(i < length(ts_arr), ts_arr[i+1], toDateTime64('2099-01-01',3,'UTC')),
            addSeconds(ts_arr[i], 90)
        ), arrayEnumerate(ts_arr)) AS seg_end,
        arrayMap(i -> if(fg[i]=1 AND playing[i]=1 AND ended[i]=0, 1, 0),
                 arrayEnumerate(ts_arr)) AS is_active
    FROM gates
),

-- Step 7: Extract active segments
active_segments AS (
    SELECT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event,
        seg.1 AS seg_start, seg.2 AS seg_stop
    FROM segmented
    ARRAY JOIN arrayFilter(x -> x.3=1,
        arrayMap(i -> (ts_arr[i], seg_end[i], is_active[i]), arrayEnumerate(ts_arr))) AS seg
),

-- Step 8: Explode to minutes + deduplicate per (session, minute)
active_session_minutes AS (
    SELECT DISTINCT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event,
        arrayJoin(arrayMap(x -> toStartOfMinute(seg_start) + toIntervalMinute(x),
            range(toUInt32(greatest(
                dateDiff('minute', toStartOfMinute(seg_start), toStartOfMinute(seg_stop)) + 1, 1
            )))
        )) AS minute
    FROM active_segments
),

-- Step 9: Merge contiguous minutes into runs
active_with_groups AS (
    SELECT *,
        toInt64(toUnixTimestamp(minute)) -
            toInt64(row_number() OVER (PARTITION BY video_session_id ORDER BY minute)) * 60 AS run_group
    FROM active_session_minutes
),

active_runs AS (
    SELECT video_session_id, user_id, platform, country, content_id, video_resolution,
           session_first_event, session_last_event,
           min(minute) AS run_start, max(minute) + toIntervalMinute(1) AS run_end
    FROM active_with_groups
    GROUP BY video_session_id, user_id, platform, country, content_id, video_resolution,
             session_first_event, session_last_event, run_group
),

-- Step 10: Active deltas (+1 at run start, -1 at run end)
active_deltas AS (
    SELECT video_session_id, user_id, run_start AS minute,
           platform, country, video_resolution, content_id,
           toInt8(1) AS delta_sessions, toInt8(0) AS delta_open
    FROM active_runs
    UNION ALL
    SELECT video_session_id, user_id, run_end AS minute,
           platform, country, video_resolution, content_id,
           toInt8(-1) AS delta_sessions, toInt8(0) AS delta_open
    FROM active_runs
),

-- Step 11: Open session deltas (+1 at start, -1 at last_event+90s)
open_deltas AS (
    SELECT DISTINCT video_session_id, user_id,
        toStartOfMinute(session_first_event) AS minute,
        platform, country, video_resolution, content_id,
        toInt8(0) AS delta_sessions, toInt8(1) AS delta_open
    FROM sorted
    UNION ALL
    SELECT DISTINCT video_session_id, user_id,
        toStartOfMinute(addSeconds(session_last_event, 90)) + toIntervalMinute(1) AS minute,
        platform, country, video_resolution, content_id,
        toInt8(0) AS delta_sessions, toInt8(-1) AS delta_open
    FROM sorted
),

-- Step 12: Combine + merge at same (session, minute)
all_deltas AS (
    SELECT * FROM active_deltas
    UNION ALL
    SELECT * FROM open_deltas
),

merged_deltas AS (
    SELECT
        video_session_id, any(user_id) AS user_id, minute,
        any(platform) AS platform, any(country) AS country,
        any(video_resolution) AS video_resolution, any(content_id) AS content_id,
        toInt8(sum(delta_sessions)) AS delta_sessions,
        toInt8(sum(delta_open)) AS delta_open
    FROM all_deltas
    GROUP BY video_session_id, minute
),

-- Step 13: Tombstones for stale minutes
session_ranges AS (
    SELECT DISTINCT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event
    FROM sorted
),

tombstone_minutes AS (
    SELECT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        arrayJoin(arrayMap(x -> toStartOfMinute(session_first_event) + toIntervalMinute(x),
            range(toUInt32(
                dateDiff('minute', toStartOfMinute(session_first_event),
                         toStartOfMinute(addSeconds(session_last_event, 90))) + 2
            ))
        )) AS minute
    FROM session_ranges
),

tombstones AS (
    SELECT t.video_session_id, t.user_id, t.minute,
           t.platform, t.country, t.video_resolution, t.content_id,
           toInt8(0) AS delta_sessions, toInt8(0) AS delta_open
    FROM tombstone_minutes t
    LEFT ANTI JOIN merged_deltas m
        ON m.video_session_id = t.video_session_id AND m.minute = t.minute
)

-- Output
SELECT video_session_id, user_id, minute, platform, country, video_resolution,
       content_id, delta_sessions, delta_open, now64(3) AS computed_at
FROM merged_deltas
UNION ALL
SELECT video_session_id, user_id, minute, platform, country, video_resolution,
       content_id, delta_sessions, delta_open, now64(3) AS computed_at
FROM tombstones;
