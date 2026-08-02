-- Migration 005: Concurrency deltas fact table + recompute MV + checkpoint advance
--
-- Stores per-session +1/-1 deltas. Content-derived dimensions (video_type,
-- category, show_name) NOT stored — look up via dict_content at query time.

CREATE TABLE IF NOT EXISTS fact_concurrency_deltas
(
    video_session_id String,
    user_id          String,
    minute           DateTime,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       Int64,
    delta_sessions   Int8,
    delta_open       Int8,
    computed_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toDate(minute)
ORDER BY (minute, video_session_id)
TTL toDate(minute) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;


-- Main concurrency MV: processes only sessions pending since last checkpoint
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_compute_concurrency
REFRESH EVERY 30 SECOND APPEND
TO fact_concurrency_deltas
AS
WITH
changed AS (
    SELECT DISTINCT video_session_id
    FROM raw_sessions_pending
    WHERE ingest_ts > (SELECT last_processed_ts FROM processing_checkpoint FINAL WHERE id = 1)
      AND ingest_ts <= now64(3) - INTERVAL 5 SECOND
),
deduped AS (
    SELECT DISTINCT
        video_session_id, user_id, event_ts, event_type, event,
        platform, country, content_id, video_resolution
    FROM fact_events
    WHERE video_session_id IN (SELECT video_session_id FROM changed)
),
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
active_segments AS (
    SELECT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event,
        seg.1 AS seg_start, seg.2 AS seg_stop
    FROM segmented
    ARRAY JOIN arrayFilter(x -> x.3=1,
        arrayMap(i -> (ts_arr[i], seg_end[i], is_active[i]), arrayEnumerate(ts_arr))) AS seg
),
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
-- Full range: emit a row for EVERY minute in the session's span.
-- Minutes with no delta get 0. This overwrites any stale rows from prior cycles
-- without needing a separate tombstone step.
session_ranges AS (
    SELECT DISTINCT
        video_session_id, user_id, platform, country, content_id, video_resolution,
        session_first_event, session_last_event
    FROM sorted
),
full_range AS (
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
-- Join full range with actual deltas: minutes with deltas get their values,
-- minutes without get 0/0 (overwrites stale data from prior computations)
output AS (
    SELECT
        fr.video_session_id, fr.user_id, fr.minute,
        fr.platform, fr.country, fr.video_resolution, fr.content_id,
        coalesce(md.delta_sessions, toInt8(0)) AS delta_sessions,
        coalesce(md.delta_open, toInt8(0)) AS delta_open
    FROM full_range fr
    LEFT JOIN merged_deltas md
        ON md.video_session_id = fr.video_session_id AND md.minute = fr.minute
)
SELECT video_session_id, user_id, minute, platform, country, video_resolution,
       content_id, delta_sessions, delta_open, now64(3) AS computed_at
FROM output;


-- Checkpoint advance MV: fires 10s after the main MV
-- Advances to max(ingest_ts) that was within the processable window
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_advance_checkpoint
REFRESH EVERY 30 SECOND OFFSET 10 SECOND APPEND
TO processing_checkpoint
AS
SELECT
    toUInt8(1) AS id,
    max(ingest_ts) AS last_processed_ts,
    now64(3) AS updated_at
FROM raw_sessions_pending
WHERE ingest_ts <= now64(3) - INTERVAL 5 SECOND;
