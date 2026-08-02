-- Migration 004: Processing checkpoint + pending session tracking

-- Checkpoint: tracks last processed ingest_ts. Single row, ReplacingMergeTree.
CREATE TABLE IF NOT EXISTS processing_checkpoint
(
    id                UInt8 DEFAULT 1,
    last_processed_ts DateTime64(3, 'UTC'),
    updated_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY id;

-- Seed to epoch so first cycle processes everything
INSERT INTO processing_checkpoint (id, last_processed_ts)
VALUES (1, toDateTime64('1970-01-01 00:00:00.000', 3, 'UTC'));

-- Pending sessions: populated on each INSERT, keyed by arrival time
CREATE TABLE IF NOT EXISTS raw_sessions_pending
(
    video_session_id String,
    ingest_ts        DateTime64(3, 'UTC')
)
ENGINE = MergeTree()
ORDER BY (ingest_ts, video_session_id)
TTL ingest_ts + INTERVAL 10 MINUTE;

-- MV: marks session pending with arrival timestamp
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_flag_pending TO raw_sessions_pending AS
SELECT video_session_id, now64(3) AS ingest_ts
FROM raw_events_ingest;
