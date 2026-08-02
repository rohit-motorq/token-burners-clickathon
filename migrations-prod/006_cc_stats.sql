-- Migration 006: Concurrency stats table + MV (reads from fact_concurrency_deltas)
--
-- Provides pre-aggregated per-minute counts + HLL user sketches.
-- Content dimensions (video_type, category, show_name) available via dict at query time.

CREATE TABLE IF NOT EXISTS fact_concurrency_stats
(
    minute           DateTime,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       UInt64,
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
GROUP BY minute, platform, country, video_resolution, content_id;
