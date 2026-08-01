-- Migration 006: Session active tracking + MV
-- Tracks which sessions have an open +1.
-- Updated atomically via MV chained on cc_delta_raw.
CREATE TABLE IF NOT EXISTS session_active
(
    video_session_id String,
    content_id       UInt64,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_type       LowCardinality(String),
    category         LowCardinality(String),
    title            String,
    last_seen        DateTime64(3, 'UTC'),
    is_active        UInt8 DEFAULT 1,
    version          UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (last_seen, video_session_id)
TTL toDateTime(last_seen) + INTERVAL 7 DAY;

-- MV: cc_delta_raw → session_active (syncs is_active and last_seen atomically)
CREATE MATERIALIZED VIEW IF NOT EXISTS session_active_sync_mv TO session_active AS
SELECT
    video_session_id,
    content_id,
    platform,
    country,
    video_type,
    category,
    title,
    minute AS last_seen,
    -- +1 → is_active=1, -1 → is_active=0, keepalive (0) → is_active=1
    if(delta_sessions >= 0, toUInt8(1), toUInt8(0)) AS is_active,
    toUInt64(toUnixTimestamp64Milli(toDateTime64(minute, 3, 'UTC'))) + if(delta_sessions < 0, 1, 0) AS version
FROM cc_delta_raw;
