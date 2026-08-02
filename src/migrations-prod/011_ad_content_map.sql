-- Migration 011: Advertiser <-> content mapping (billing genre attribution)
--
-- Why content_id and not just a time range: see src/agent/INNER_CONTEXT.md
-- for the full reasoning. Short version — advertisers buy inventory on
-- specific content (pre-roll/mid-roll slots), not a blanket hour on the
-- platform. Multiple pieces of content air concurrently, and the same
-- content can rotate between multiple advertisers within one hour. A
-- time-range-only query can't split that traffic; content_id is the
-- minimum key that disambiguates which sessions actually saw which ad.
--
-- Seed data revised: the original seed used content_ids from
-- ch_hackathon_content_data (a `default`-database-only table, and mostly
-- near-zero real activity even there). rohitdevtesting's real cc_delta_content
-- data has its own high-activity content_ids — reseeded against those so the
-- BILLING genre's eval fixture returns a genuine nonzero number instead of
-- "0 impressions" for a content_id/window with no real activity at all.

CREATE TABLE IF NOT EXISTS ad_content_map
(
    advertiser_id UInt64,
    content_id    UInt64,
    updated_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (advertiser_id, content_id);

-- content_ids are real, verified-live rows in rohitdevtesting's cc_delta_content
-- (checked: 306/170/144/127/117 rows respectively, spanning into 2026-07-26 11:30).
-- 2078157818 deliberately mapped to two advertisers (1002 and 1003) to model
-- the "same content, rotating sponsors within the hour" case.
INSERT INTO ad_content_map (advertiser_id, content_id) VALUES
    (1001, 2078158511),
    (1001, 2078158543),
    (1002, 2078157818),
    (1003, 2078157818),
    (1003, 2078158754),
    (1004, 2078158760);
