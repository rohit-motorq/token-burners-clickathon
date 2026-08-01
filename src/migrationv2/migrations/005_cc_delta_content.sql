-- Migration 005: Concurrency delta table + MV
-- Minute-level, per dimension. No video_session_id.
-- SummingMergeTree collapses rows with same ORDER BY key.
CREATE TABLE IF NOT EXISTS cc_delta_content
(
    minute         DateTime('UTC'),
    content_id     UInt64,
    platform       LowCardinality(String),
    country        LowCardinality(String),
    video_type     LowCardinality(String),
    category       LowCardinality(String),
    title          String,
    delta_sessions Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (minute, content_id, platform, country, video_type, category);

-- MV: cc_delta_raw → cc_delta_content (strips video_session_id, skips keepalives)
CREATE MATERIALIZED VIEW IF NOT EXISTS cc_delta_content_mv TO cc_delta_content AS
SELECT minute, content_id, platform, country, video_type, category, title, delta_sessions
FROM cc_delta_raw
WHERE delta_sessions != 0;
