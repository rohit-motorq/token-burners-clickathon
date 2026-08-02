-- Migration 008: Estimated content end time, derived from fact_concurrency_deltas.
--
-- No real programming-schedule data exists anywhere in this dataset. Rather
-- than leave DIAGNOSTIC's "did content end" check permanently unanswerable,
-- derive a placeholder from the last observed deactivation (delta_sessions
-- = -1) per content_id, explicitly flagged as an estimate. Port of
-- migrationv2's 010_content_estimated_end.sql, adapted to this pipeline's
-- REFRESH-MV pattern (no per-insert trigger MVs here — see 005/006) and to
-- fact_concurrency_deltas' schema (content dims via dict_content, not
-- direct columns).

ALTER TABLE dim_content
    ADD COLUMN IF NOT EXISTS scheduled_end_ts Nullable(DateTime('UTC')),
    ADD COLUMN IF NOT EXISTS end_ts_is_estimated UInt8 DEFAULT 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_content_estimated_end
REFRESH EVERY 30 SECOND OFFSET 20 SECOND
TO dim_content
AS
SELECT
    content_id,
    dictGetOrDefault('dict_content', 'title', content_id, 'unknown') AS title,
    dictGetOrDefault('dict_content', 'video_type', content_id, 'unknown') AS video_type,
    dictGetOrDefault('dict_content', 'category', content_id, 'unknown') AS category,
    dictGetOrDefault('dict_content', 'show_name', content_id, 'unknown') AS show_name,
    max(minute) AS scheduled_end_ts,
    1 AS end_ts_is_estimated,
    now() AS updated_at
FROM fact_concurrency_deltas FINAL
WHERE delta_sessions = -1
GROUP BY content_id;
