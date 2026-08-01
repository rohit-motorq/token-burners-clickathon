-- Migration 008: Delta fold — Refreshable MV (every 20s) → cc_delta_raw
--
-- Reads events_raw since last checkpoint + checks session_active.
-- Emits:
--   +1: VideoPlay/resume AND session NOT already active
--   -1 (hard): VideoSessionEnd AND session WAS active
--   -1 (soft): session_active with is_active=1, silent >90s
--
-- Writes to cc_delta_raw (Null engine). Two chained MVs fire atomically:
--   → cc_delta_content (stores the delta)
--   → session_active (updates is_active flag)

CREATE MATERIALIZED VIEW IF NOT EXISTS delta_fold_mv
REFRESH EVERY 30 SECOND APPEND
TO cc_delta_raw
AS
WITH
    last_cp AS (
        SELECT argMax(checkpoint_ts, version) AS ts
        FROM pipeline_checkpoint
        WHERE pipeline_name = 'delta_fold'
    ),

    new_events AS (
        SELECT video_session_id, content_id, event_type, event, event_ts,
               platform, country, video_type, category, title
        FROM events_raw
        WHERE ingest_ts > (SELECT ts FROM last_cp)
    ),

    touched AS (
        SELECT DISTINCT video_session_id FROM new_events
    ),

    prior_active AS (
        SELECT video_session_id,
               argMax(is_active, version) AS is_active,
               argMax(last_seen, version) AS last_seen
        FROM session_active
        WHERE video_session_id IN (SELECT video_session_id FROM touched)
        GROUP BY video_session_id
    ),

    per_session AS (
        SELECT
            e.video_session_id,
            any(e.content_id) AS content_id,
            any(e.platform) AS platform,
            any(e.country) AS country,
            any(e.video_type) AS video_type,
            any(e.category) AS category,
            any(e.title) AS title,
            ifNull(a.is_active, toUInt8(0)) AS was_active,
            -- First activation event in this batch
            min(if(
                e.event_type = 'VideoPlay'
                OR ((e.event_type = 'VideoHeartbeat') AND (e.event IN ('play', 'resume'))),
                e.event_ts,
                toDateTime64('2100-01-01', 3, 'UTC')
            )) AS activation_ts,
            -- Session end event
            max(if(e.event_type = 'VideoSessionEnd', e.event_ts, toDateTime64('1970-01-01', 3, 'UTC'))) AS end_ts
        FROM new_events e
        LEFT JOIN prior_active a ON e.video_session_id = a.video_session_id
        GROUP BY e.video_session_id, a.is_active
    ),

    -- +1: has activation AND was NOT active
    plus_ones AS (
        SELECT
            toStartOfMinute(activation_ts) AS minute,
            video_session_id,
            content_id, platform, country, video_type, category, title,
            toInt64(1) AS delta_sessions
        FROM per_session
        WHERE activation_ts < toDateTime64('2100-01-01', 3, 'UTC')
          AND was_active = 0
    ),

    -- Keepalive: active sessions with new events but no state change
    -- Writes delta_sessions=0 which updates last_seen in session_active via MV
    -- but does NOT affect cc_delta_content (SummingMergeTree ignores 0s)
    keepalives AS (
        SELECT
            toStartOfMinute(max(e.event_ts)) AS minute,
            e.video_session_id,
            any(e.content_id) AS content_id,
            any(e.platform) AS platform,
            any(e.country) AS country,
            any(e.video_type) AS video_type,
            any(e.category) AS category,
            any(e.title) AS title,
            toInt64(0) AS delta_sessions
        FROM new_events e
        INNER JOIN prior_active a ON e.video_session_id = a.video_session_id
        WHERE a.is_active = 1
          AND e.event_type NOT IN ('AppBackgrounded', 'VideoSessionEnd')
          AND NOT ((e.event_type = 'VideoHeartbeat') AND (e.event IN ('pause', 'download_start', 'download_complete', 'download_initiated', 'download_completed', 'download_deleted')))
        GROUP BY e.video_session_id
    ),

    -- -1 (hard): ended AND (was active OR just activated in this batch)
    minus_hard AS (
        SELECT
            toStartOfMinute(end_ts) AS minute,
            video_session_id,
            content_id, platform, country, video_type, category, title,
            toInt64(-1) AS delta_sessions
        FROM per_session
        WHERE end_ts > toDateTime64('1970-01-01', 3, 'UTC')
          AND (was_active = 1
               OR activation_ts < toDateTime64('2100-01-01', 3, 'UTC'))
    ),

    -- -1 (soft): is_active=1, silent >90s, NOT in current batch
    minus_sweep AS (
        SELECT
            toStartOfMinute(addSeconds(last_seen, 90)) AS minute,
            video_session_id,
            content_id, platform, country, video_type, category, title,
            toInt64(-1) AS delta_sessions
        FROM (
            SELECT video_session_id,
                   argMax(content_id, version) AS content_id,
                   argMax(platform, version) AS platform,
                   argMax(country, version) AS country,
                   argMax(video_type, version) AS video_type,
                   argMax(category, version) AS category,
                   argMax(title, version) AS title,
                   argMax(last_seen, version) AS last_seen,
                   argMax(is_active, version) AS is_active
            FROM session_active
            GROUP BY video_session_id
        )
        WHERE video_session_id NOT IN (SELECT video_session_id FROM touched)
          AND is_active = 1
          AND last_seen < now64(3) - INTERVAL 90 SECOND
          AND last_seen > toDateTime64('1970-01-01 00:00:00.000', 3, 'UTC')
    )

SELECT * FROM plus_ones
UNION ALL
SELECT * FROM keepalives
UNION ALL
SELECT * FROM minus_hard
UNION ALL
SELECT * FROM minus_sweep;
