-- Migration 002: Content dimension table + dictionary

CREATE TABLE IF NOT EXISTS dim_content
(
    content_id Int64,
    title      String,
    video_type LowCardinality(String),
    category   LowCardinality(String),
    show_name  String DEFAULT 'unknown',
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY content_id;

CREATE DICTIONARY IF NOT EXISTS dict_content
(
    content_id Int64,
    title      String DEFAULT 'unknown',
    video_type String DEFAULT 'unknown',
    category   String DEFAULT 'unknown',
    show_name  String DEFAULT 'unknown'
)
PRIMARY KEY content_id
SOURCE(CLICKHOUSE(TABLE 'dim_content' USER 'default' PASSWORD 'DApBb4.O_9tqI'))
LAYOUT(HASHED())
LIFETIME(MIN 60 MAX 300);
