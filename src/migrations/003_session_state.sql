-- Migration 003: Session state table
-- One row per session. ReplacingMergeTree(ver) keeps the latest.
-- Read with argMax(col, ver) GROUP BY video_session_id.

CREATE TABLE IF NOT EXISTS session_state
(
    video_session_id String,
    user_id          String,
    content_id       UInt64,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_type       LowCardinality(String),
    category         LowCardinality(String),
    title            String,
    fg               UInt8 DEFAULT 1,
    playing          UInt8 DEFAULT 0,
    ended            UInt8 DEFAULT 0,
    last_seen        DateTime64(3, 'UTC'),
    open_run_start   Nullable(DateTime64(3, 'UTC')),
    ver              UInt64
)
ENGINE = ReplacingMergeTree(ver)
ORDER BY video_session_id
TTL toDateTime(last_seen) + INTERVAL 7 DAY;

ALTER TABLE session_state
ADD INDEX IF NOT EXISTS idx_last_seen last_seen TYPE minmax GRANULARITY 4;
