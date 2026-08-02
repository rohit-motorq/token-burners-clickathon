-- Migration 004: Pending session tracking (table + MV)

CREATE TABLE IF NOT EXISTS raw_sessions_pending
(
    video_session_id String,
    arrived_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree()
ORDER BY video_session_id
TTL arrived_at + INTERVAL 5 MINUTE;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_flag_pending TO raw_sessions_pending AS
SELECT video_session_id, now64(3) AS arrived_at
FROM raw_events_ingest;
