-- Migration 001: Raw ingestion endpoint (Null engine)
-- Column order matches the unseen-day CSV header exactly.
-- ClickPipes maps positionally, so order matters.

CREATE TABLE IF NOT EXISTS raw_events_ingest
(
    content_id        String,
    video_session_id  String,
    user_id           String,
    event_type        LowCardinality(String),
    event             LowCardinality(String),
    event_timestamp   String,
    platform          LowCardinality(String),
    app_version       LowCardinality(String),
    country           LowCardinality(String),
    audio_language    LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start_epoch String,
    video_resolution  LowCardinality(String)
)
ENGINE = Null;
