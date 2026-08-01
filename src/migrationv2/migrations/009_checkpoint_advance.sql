-- Migration 009: Checkpoint advance — Refreshable MV (every 35s)
-- Runs slightly slower than the fold (30s) to ensure fold processes first.
CREATE MATERIALIZED VIEW IF NOT EXISTS checkpoint_advance_mv
REFRESH EVERY 35 SECOND APPEND
TO pipeline_checkpoint
AS
SELECT
    'delta_fold' AS pipeline_name,
    max(ingest_ts) AS checkpoint_ts,
    toUInt64(toUnixTimestamp64Milli(now64(3))) AS version
FROM events_raw
WHERE ingest_ts > (
    SELECT argMax(checkpoint_ts, version)
    FROM pipeline_checkpoint
    WHERE pipeline_name = 'delta_fold'
)
HAVING count() > 0;
