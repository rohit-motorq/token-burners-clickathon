-- Migration 009: Advertiser <-> content mapping (billing genre attribution)
--
-- Why content_id and not just a time range: see src/agent/INNER_CONTEXT.md.
-- Advertisers buy inventory on specific content, not a blanket hour on the
-- platform; content_id is the minimum key that disambiguates which sessions
-- actually saw which ad. Port of migrationv2's 011_ad_content_map.sql —
-- table shape unchanged, reseeded against this pipeline's real content_ids.

CREATE TABLE IF NOT EXISTS ad_content_map
(
    advertiser_id UInt64,
    content_id    UInt64,
    updated_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (advertiser_id, content_id);

-- content_ids re-pulled live from this instance's fact_concurrency_deltas
-- (top session-start volume, 2026-08-02) — the prior seed's ids (20971521
-- etc) belong to migrationv2's dataset and don't exist under migrations-prod,
-- so every BILLING query against them silently returned zero impressions.
-- 2078157821 deliberately mapped to two advertisers (1002 and 1003) to model
-- the "same content, rotating sponsors within the hour" case.
INSERT INTO ad_content_map (advertiser_id, content_id) VALUES
    (1001, 2078157818),
    (1001, 2078157680),
    (1002, 2078155112),
    (1002, 2078155114),
    (1002, 2078157821),
    (1003, 2078157821),
    (1003, 2078157683),
    (1004, 2078155219);
