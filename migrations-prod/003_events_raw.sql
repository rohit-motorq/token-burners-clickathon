-- Migration 003: Enriched events fact table + ingestion MV

CREATE TABLE IF NOT EXISTS fact_events
(
    video_session_id  String,
    user_id           String,
    content_id        UInt64,
    event_type        LowCardinality(String) DEFAULT 'unknown',
    event             LowCardinality(String) DEFAULT 'unknown',
    event_ts          DateTime64(3, 'UTC'),
    platform          LowCardinality(String) DEFAULT 'unknown',
    app_version       LowCardinality(String) DEFAULT 'unknown',
    country           LowCardinality(String) DEFAULT 'unknown',
    audio_language    LowCardinality(String) DEFAULT 'unknown',
    subtitle_language LowCardinality(String) DEFAULT 'unknown',
    player_version    LowCardinality(String) DEFAULT 'unknown',
    video_resolution  LowCardinality(String) DEFAULT 'unknown',
    session_start     DateTime64(3, 'UTC'),
    title             String,
    video_type        LowCardinality(String),
    category          LowCardinality(String),
    show_name         String DEFAULT 'unknown',
    ingest_ts         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree()
PARTITION BY toDate(session_start)
ORDER BY (video_session_id, event_ts, event_type, event)
TTL toDate(session_start) + INTERVAL 45 DAY
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ingest_to_fact TO fact_events AS
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
    video_resolution,
    fromUnixTimestamp64Milli(toInt64(session_start_epoch)) AS session_start,
    dictGetOrDefault('dict_content', 'title', toUInt64(content_id), 'unknown') AS title,
    dictGetOrDefault('dict_content', 'video_type', toUInt64(content_id), 'unknown') AS video_type,
    dictGetOrDefault('dict_content', 'category', toUInt64(content_id), 'unknown') AS category,
    dictGetOrDefault('dict_content', 'show_name', toUInt64(content_id), 'unknown') AS show_name,
    now64(3) AS ingest_ts
FROM raw_events_ingest;
