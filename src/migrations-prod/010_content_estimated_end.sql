-- Migration 010: Estimated content end time, derived from cc_delta_raw.
--
-- No real programming-schedule data exists anywhere in this dataset. Rather
-- than leave DIAGNOSTIC's "did content end" check permanently unanswerable,
-- derive a placeholder from the last observed deactivation per content_id,
-- explicitly flagged as an estimate (see src/agent/INNER_CONTEXT.md for the
-- real-world reasoning behind why this check matters at all).
--
-- This is the migrationv2-schema version of the same idea in
-- src/migrations/009_content_estimated_end.sql (which sources session_runs —
-- a table that doesn't exist under this pipeline). Here the source is
-- cc_delta_raw (Null engine): every session deactivation, hard-end or
-- soft-sweep, already lands there as a negative delta_sessions row (see
-- 008_delta_fold.sql's minus_hard/minus_sweep), so this is a THIRD MV
-- chained off the same source table 005/006 already chain off — same
-- pattern, incremental, no full rebuild.

ALTER TABLE content_dim
    ADD COLUMN IF NOT EXISTS scheduled_end_ts Nullable(DateTime('UTC')),
    ADD COLUMN IF NOT EXISTS end_ts_is_estimated UInt8 DEFAULT 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS content_estimated_end_mv TO content_dim AS
SELECT
    content_id,
    dictGetOrDefault('content_dict', 'title', content_id, 'unknown') AS title,
    dictGetOrDefault('content_dict', 'video_type', content_id, 'unknown') AS video_type,
    dictGetOrDefault('content_dict', 'category', content_id, 'unknown') AS category,
    minute AS scheduled_end_ts,
    1 AS end_ts_is_estimated,
    now() AS updated_at
FROM cc_delta_raw
WHERE delta_sessions < 0;
