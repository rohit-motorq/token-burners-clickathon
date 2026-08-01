-- Migration 004: Intermediate delta table (Null engine)
-- The fold writes here. Two chained MVs fire atomically on INSERT.
CREATE TABLE IF NOT EXISTS cc_delta_raw
(
    minute             DateTime('UTC'),
    video_session_id   String,
    content_id         UInt64,
    platform           LowCardinality(String),
    country            LowCardinality(String),
    video_type         LowCardinality(String),
    category           LowCardinality(String),
    title              String,
    delta_sessions     Int64
)
ENGINE = Null;
