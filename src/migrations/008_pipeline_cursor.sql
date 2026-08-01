-- Migration 008: Pipeline cursor (batch progress marker)
CREATE TABLE IF NOT EXISTS pipeline_cursor
(
    name      LowCardinality(String),
    cursor_ts DateTime64(3, 'UTC'),
    ver       UInt64
)
ENGINE = ReplacingMergeTree(ver)
ORDER BY name;

INSERT INTO pipeline_cursor (name, cursor_ts, ver) VALUES
    ('batch_fold', '1970-01-01 00:00:00.000', 0),
    ('sweep', '1970-01-01 00:00:00.000', 0);
