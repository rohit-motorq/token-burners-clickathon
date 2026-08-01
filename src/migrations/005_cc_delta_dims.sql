-- Migration 005: Narrow delta table (no content_id) + fan-out MV
-- For dashboard queries that filter by platform/country/video_type.
-- Auto-populated by MV on INSERT to cc_delta_content.

CREATE TABLE IF NOT EXISTS cc_delta_dims
(
    minute         DateTime('UTC'),
    platform       LowCardinality(String),
    country        LowCardinality(String),
    video_type     LowCardinality(String),
    category       LowCardinality(String),
    delta_sessions Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (minute, platform, country, video_type, category);

CREATE MATERIALIZED VIEW IF NOT EXISTS cc_delta_dims_mv TO cc_delta_dims AS
SELECT minute, platform, country, video_type, category, delta_sessions
FROM cc_delta_content;
