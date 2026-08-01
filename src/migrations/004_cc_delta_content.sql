-- Migration 004: Concurrency delta table
-- +1/-1 per session activation/deactivation at minute granularity.
-- SummingMergeTree collapses same-key rows during merges.
-- Query: SELECT minute, sum(delta_sessions) GROUP BY minute

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
