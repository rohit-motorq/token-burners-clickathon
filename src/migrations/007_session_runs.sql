-- Migration 007: Session runs audit ledger
-- Every emitted +1/-1 pair recorded here for retraction support.

CREATE TABLE IF NOT EXISTS session_runs
(
    video_session_id String,
    run_start        DateTime('UTC'),
    run_end          DateTime('UTC'),
    content_id       UInt64,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_type       LowCardinality(String),
    category         LowCardinality(String),
    sign             Int8 DEFAULT 1
)
ENGINE = MergeTree
PARTITION BY toDate(run_start)
ORDER BY (video_session_id, run_start);
