-- Migration 007: Pipeline checkpoint (cursor)
CREATE TABLE IF NOT EXISTS pipeline_checkpoint
(
    pipeline_name  LowCardinality(String),
    checkpoint_ts  DateTime64(3, 'UTC'),
    version        UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY pipeline_name;

INSERT INTO pipeline_checkpoint (pipeline_name, checkpoint_ts, version) VALUES
    ('delta_fold', '1970-01-01 00:00:00.000', 0);
