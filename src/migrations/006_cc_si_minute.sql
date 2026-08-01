-- Migration 006: Session-independent concurrency (HLL sketches)
-- Fires per INSERT to events_raw. Stateless.
-- Stores uniqCombined sketches per minute (distinct sessions/users).

CREATE TABLE IF NOT EXISTS cc_si_minute
(
    minute         DateTime('UTC'),
    content_id     UInt64,
    platform       LowCardinality(String),
    country        LowCardinality(String),
    video_type     LowCardinality(String),
    sessions_state AggregateFunction(uniqCombined(14), String),
    users_state    AggregateFunction(uniqCombined(14), String)
)
ENGINE = AggregatingMergeTree
PARTITION BY toDate(minute)
ORDER BY (minute, platform, country, video_type, content_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS cc_si_minute_mv TO cc_si_minute AS
SELECT
    toStartOfMinute(event_ts) AS minute,
    content_id,
    platform,
    country,
    dictGetOrDefault('content_dict', 'video_type', content_id, 'unknown') AS video_type,
    uniqCombinedState(14)(video_session_id) AS sessions_state,
    uniqCombinedState(14)(user_id)          AS users_state
FROM events_raw
WHERE NOT (event = 'pause' OR event LIKE 'download%'
        OR event_type IN ('AppBackgrounded', 'VideoSessionEnd'))
GROUP BY minute, content_id, platform, country, video_type;
