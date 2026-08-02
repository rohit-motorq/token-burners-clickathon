-- Migration 007: Hourly and daily rollup tables (from fact_concurrency_stats)
--
-- These roll up the minute-level stats into coarser grains for fast dashboard queries.
-- Peak = max(active_sessions) within the hour/day — NOT a sum.
-- Users = merged HLL sketches — correctly deduplicates across minutes.


-- ============================================================
-- HOURLY ROLLUP
-- ============================================================
CREATE TABLE IF NOT EXISTS fact_concurrency_stats_hourly
(
    hour             DateTime,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       Int64,
    peak_active_sessions  Int32,
    peak_open_sessions    Int32,
    avg_active_sessions   Float32,
    avg_open_sessions     Float32,
    active_users     AggregateFunction(uniq, String),
    open_users       AggregateFunction(uniq, String),
    computed_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toDate(hour)
ORDER BY (hour, platform, country, video_resolution, content_id)
TTL toDate(hour) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_hourly
REFRESH EVERY 1 HOUR OFFSET 5 MINUTE
TO fact_concurrency_stats_hourly
AS
SELECT
    toStartOfHour(minute) AS hour,
    platform,
    country,
    video_resolution,
    content_id,
    max(active_sessions) AS peak_active_sessions,
    max(open_sessions) AS peak_open_sessions,
    toFloat32(avg(active_sessions)) AS avg_active_sessions,
    toFloat32(avg(open_sessions)) AS avg_open_sessions,
    uniqMergeState(active_users) AS active_users,
    uniqMergeState(open_users) AS open_users,
    now64(3) AS computed_at
FROM fact_concurrency_stats FINAL
GROUP BY hour, platform, country, video_resolution, content_id;


-- ============================================================
-- DAILY ROLLUP
-- ============================================================
CREATE TABLE IF NOT EXISTS fact_concurrency_stats_daily
(
    day              Date,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_resolution LowCardinality(String) DEFAULT 'unknown',
    content_id       Int64,
    peak_active_sessions  Int32,
    peak_open_sessions    Int32,
    avg_active_sessions   Float32,
    avg_open_sessions     Float32,
    active_users     AggregateFunction(uniq, String),
    open_users       AggregateFunction(uniq, String),
    computed_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYear(day)
ORDER BY (day, platform, country, video_resolution, content_id)
TTL day + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_daily
REFRESH EVERY 1 DAY OFFSET 10 MINUTE
TO fact_concurrency_stats_daily
AS
SELECT
    toDate(minute) AS day,
    platform,
    country,
    video_resolution,
    content_id,
    max(active_sessions) AS peak_active_sessions,
    max(open_sessions) AS peak_open_sessions,
    toFloat32(avg(active_sessions)) AS avg_active_sessions,
    toFloat32(avg(open_sessions)) AS avg_open_sessions,
    uniqMergeState(active_users) AS active_users,
    uniqMergeState(open_users) AS open_users,
    now64(3) AS computed_at
FROM fact_concurrency_stats FINAL
GROUP BY day, platform, country, video_resolution, content_id;
