-- ============================================================
-- ALL MIGRATIONS — Single file for fresh deployment
-- Run statements one by one (ClickHouse doesn't support multi-statement)
-- ============================================================


-- ============================================================
-- 001: Raw ingestion endpoint (Null engine)
-- Column order matches the unseen-day CSV header exactly.
-- ClickPipes maps positionally, so order matters.
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_events_ingest
(
    content_id        String,
    video_session_id  String,
    user_id           String,
    event_type        LowCardinality(String),
    event             LowCardinality(String),
    event_timestamp   String,
    platform          LowCardinality(String),
    app_version       LowCardinality(String),
    country           LowCardinality(String),
    audio_language    LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start_epoch String,
    video_resolution  LowCardinality(String)
)
ENGINE = Null;


-- ============================================================
-- 002: Content dimension table
-- ============================================================
CREATE TABLE IF NOT EXISTS dim_content
(
    content_id Int64,
    title      String,
    video_type LowCardinality(String),
    category   LowCardinality(String),
    show_name  String DEFAULT 'unknown',
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY content_id;


-- ============================================================
-- 002b: Content dictionary
-- ============================================================
CREATE DICTIONARY IF NOT EXISTS dict_content
(
    content_id Int64,
    title      String DEFAULT 'unknown',
    video_type String DEFAULT 'unknown',
    category   String DEFAULT 'unknown',
    show_name  String DEFAULT 'unknown'
)
PRIMARY KEY content_id
SOURCE(CLICKHOUSE(TABLE 'dim_content' USER 'default' PASSWORD 'DApBb4.O_9tqI'))
LAYOUT(HASHED())
LIFETIME(MIN 60 MAX 300);


-- ============================================================
-- 003: Enriched events fact table
-- ============================================================
CREATE TABLE IF NOT EXISTS fact_events
(
    video_session_id  String,
    user_id           String,
    content_id        Int64,
    event_type        LowCardinality(String) DEFAULT 'unknown',
    event             LowCardinality(String) DEFAULT 'unknown',
    event_ts          DateTime64(3, 'UTC'),
    platform          LowCardinality(String) DEFAULT 'unknown',
    app_version       LowCardinality(String) DEFAULT 'unknown',
    country           LowCardinality(String) DEFAULT 'unknown',
    audio_language    LowCardinality(String) DEFAULT 'unknown',
    subtitle_language LowCardinality(String) DEFAULT 'unknown',
    player_version    LowCardinality(String) DEFAULT 'unknown',
    video_resolution  LowCardinality(String) DEFAULT 'unknown',
    session_start     DateTime64(3, 'UTC'),
    title             String DEFAULT 'unknown',
    video_type        LowCardinality(String) DEFAULT 'unknown',
    category          LowCardinality(String) DEFAULT 'unknown',
    show_name         String DEFAULT 'unknown',
    ingest_ts         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree()
PARTITION BY toDate(session_start)
ORDER BY (video_session_id, event_ts, event_type, event)
TTL toDate(session_start) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;


-- ============================================================
-- 003b: Ingestion MV (raw_events_ingest → fact_events)
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ingest_to_fact TO fact_events AS
SELECT
    video_session_id,
    user_id,
    toInt64(content_id) AS content_id,
    event_type,
    event,
    fromUnixTimestamp64Milli(toInt64(event_timestamp)) AS event_ts,
    platform,
    app_version,
    country,
    audio_language,
    subtitle_language,
    player_version,
    video_resolution,
    fromUnixTimestamp64Milli(toInt64(session_start_epoch)) AS session_start,
    dictGetOrDefault('dict_content', 'title', toInt64(content_id), 'unknown') AS title,
    dictGetOrDefault('dict_content', 'video_type', toInt64(content_id), 'unknown') AS video_type,
    dictGetOrDefault('dict_content', 'category', toInt64(content_id), 'unknown') AS category,
    dictGetOrDefault('dict_content', 'show_name', toInt64(content_id), 'unknown') AS show_name,
    now64(3) AS ingest_ts
FROM raw_events_ingest;


-- ============================================================
-- 004: Pending session tracking table
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_sessions_pending
(
    video_session_id String,
    arrived_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree()
ORDER BY video_session_id
TTL arrived_at + INTERVAL 5 MINUTE;


-- ============================================================
-- 004b: MV to mark sessions pending on each INSERT
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_flag_pending TO raw_sessions_pending AS
SELECT video_session_id, now64(3) AS arrived_at
FROM raw_events_ingest;


-- ============================================================
-- 005: Concurrency deltas fact table
-- ============================================================
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


-- ============================================================
-- 005b: Recompute MV (every 30s, processes only dirty sessions)
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_compute_concurrency
REFRESH EVERY 30 SECOND APPEND
TO fact_concurrency_deltas
AS
WITH
changed AS (
    SELECT DISTINCT video_session_id
    FROM raw_sessions_pending
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
SELECT video_session_id, user_id, minute, platform, country, video_resolution,
       content_id, delta_sessions, delta_open, now64(3) AS computed_at
FROM merged_deltas
UNION ALL
SELECT video_session_id, user_id, minute, platform, country, video_resolution,
       content_id, delta_sessions, delta_open, now64(3) AS computed_at
FROM tombstones;


-- ============================================================
-- 006: Concurrency stats table
-- ============================================================
CREATE TABLE IF NOT EXISTS fact_concurrency_stats
(
    minute           DateTime,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       Int64,
    active_sessions  Int32,
    open_sessions    Int32,
    active_users     AggregateFunction(uniq, String),
    open_users       AggregateFunction(uniq, String),
    computed_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toDate(minute)
ORDER BY (minute, platform, country, video_resolution, content_id)
TTL toDate(minute) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;


-- ============================================================
-- 006b: Stats MV (reads from fact_concurrency_deltas)
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_compute_stats
REFRESH EVERY 30 SECOND OFFSET 15 SECOND
TO fact_concurrency_stats
AS
WITH
active_runs AS (
    SELECT
        video_session_id, user_id, minute AS run_start,
        platform, country, video_resolution, content_id,
        leadInFrame(minute, 1, toDateTime('2099-01-01')) OVER (
            PARTITION BY video_session_id
            ORDER BY minute
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS run_end
    FROM fact_concurrency_deltas FINAL
    WHERE delta_sessions = 1
),
active_minutes AS (
    SELECT
        video_session_id, user_id, platform, country, video_resolution, content_id,
        arrayJoin(arrayMap(x -> run_start + toIntervalMinute(x),
            range(toUInt32(greatest(dateDiff('minute', run_start, run_end), 1)))
        )) AS minute
    FROM active_runs
    WHERE run_end > run_start
),
open_runs AS (
    SELECT
        video_session_id, user_id, minute AS run_start,
        platform, country, video_resolution, content_id,
        leadInFrame(minute, 1, toDateTime('2099-01-01')) OVER (
            PARTITION BY video_session_id
            ORDER BY minute
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS run_end
    FROM fact_concurrency_deltas FINAL
    WHERE delta_open = 1
),
open_minutes AS (
    SELECT
        video_session_id, user_id, platform, country, video_resolution, content_id,
        arrayJoin(arrayMap(x -> run_start + toIntervalMinute(x),
            range(toUInt32(greatest(dateDiff('minute', run_start, run_end), 1)))
        )) AS minute
    FROM open_runs
    WHERE run_end > run_start
)
SELECT
    coalesce(a.minute, o.minute) AS minute,
    coalesce(a.platform, o.platform) AS platform,
    coalesce(a.country, o.country) AS country,
    coalesce(a.video_resolution, o.video_resolution) AS video_resolution,
    coalesce(a.content_id, o.content_id) AS content_id,
    toInt32(uniqExact(a.video_session_id)) AS active_sessions,
    toInt32(uniqExact(o.video_session_id)) AS open_sessions,
    uniqState(a.user_id) AS active_users,
    uniqState(o.user_id) AS open_users,
    now64(3) AS computed_at
FROM active_minutes a
FULL OUTER JOIN open_minutes o
    ON a.video_session_id = o.video_session_id
   AND a.minute = o.minute
GROUP BY minute, platform, country, video_resolution, content_id
SETTINGS max_memory_usage = 40000000000;
