-- Migration 002: Null engine + events_raw + enrichment MV

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

CREATE TABLE IF NOT EXISTS events_raw
(
    video_session_id  String,
    user_id           String,
    content_id        UInt64,
    event_type        LowCardinality(String),
    event             LowCardinality(String),
    event_ts          DateTime64(3, 'UTC'),
    platform          LowCardinality(String),
    app_version       LowCardinality(String),
    country           LowCardinality(String),
    audio_language    LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start     DateTime64(3, 'UTC'),
    title             String,
    video_type        LowCardinality(String),
    category          LowCardinality(String),
    ingest_ts         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toDate(session_start)
ORDER BY (video_session_id, event_ts)
TTL toDate(session_start) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS events_ingest_mv TO events_raw AS
SELECT
    video_session_id,
    user_id,
    toUInt64(content_id) AS content_id,
    event_type,
    event,
    fromUnixTimestamp64Milli(toInt64(event_timestamp)) AS event_ts,
    platform,
    app_version,
    country,
    audio_language,
    subtitle_language,
    player_version,
    fromUnixTimestamp64Milli(toInt64(session_start_epoch)) AS session_start,
    dictGetOrDefault('content_dict', 'title', toUInt64(content_id), 'unknown') AS title,
    dictGetOrDefault('content_dict', 'video_type', toUInt64(content_id), 'unknown') AS video_type,
    dictGetOrDefault('content_dict', 'category', toUInt64(content_id), 'unknown') AS category,
    now64(3) AS ingest_ts
FROM events_ingest;
