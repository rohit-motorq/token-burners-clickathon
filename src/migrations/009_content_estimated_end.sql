-- Migration 009: Estimated content end time, derived from session_runs.
--
-- No real programming-schedule data exists anywhere in this dataset. Rather
-- than leave DIAGNOSTIC's "did content end" check permanently unanswerable,
-- derive a placeholder from the last observed session_runs.run_end per
-- content_id, explicitly flagged as an estimate (see src/agent/INNER_CONTEXT.md
-- for the real-world reasoning behind why this check matters at all).
--
-- Implementation: an MV on session_runs (not a batch recompute) so this stays
-- incremental — as new session runs close throughout the day, each content's
-- estimated end time ratchets forward automatically, no full rebuild. Reuses
-- content_dict (already built for enrichment) instead of self-joining
-- content_dim, matching the existing insert-trigger MV pattern.

ALTER TABLE content_dim
    ADD COLUMN IF NOT EXISTS scheduled_end_ts Nullable(DateTime('UTC')),
    ADD COLUMN IF NOT EXISTS end_ts_is_estimated UInt8 DEFAULT 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS content_estimated_end_mv TO content_dim AS
SELECT
    content_id,
    dictGetOrDefault('content_dict', 'title', content_id, 'unknown') AS title,
    dictGetOrDefault('content_dict', 'video_type', content_id, 'unknown') AS video_type,
    dictGetOrDefault('content_dict', 'category', content_id, 'unknown') AS category,
    run_end AS scheduled_end_ts,
    1 AS end_ts_is_estimated,
    now() AS updated_at
FROM session_runs
WHERE sign = 1;
