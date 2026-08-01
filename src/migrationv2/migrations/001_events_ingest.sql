-- Migration 001: Null engine ingestion endpoint
-- Matches the raw Kafka/JSON format exactly (strings for timestamps and content_id).
CREATE TABLE IF NOT EXISTS events_ingest
(
    video_session_id  String,
    user_id           String,
    content_id        String,
    event_type        LowCardinality(String),
    event             LowCardinality(String),
    event_timestamp   String,
    platform          LowCardinality(String),
    app_version       LowCardinality(String),
    country           LowCardinality(String),
    audio_language    LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start_epoch String
)
ENGINE = Null;
