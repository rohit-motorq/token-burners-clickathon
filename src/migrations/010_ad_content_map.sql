-- Migration 010: Advertiser <-> content mapping (billing genre attribution)
--
-- Why content_id and not just a time range: see src/agent/INNER_CONTEXT.md
-- for the full reasoning. Short version — advertisers buy inventory on
-- specific content (pre-roll/mid-roll slots), not a blanket hour on the
-- platform. Multiple pieces of content air concurrently, and the same
-- content can rotate between multiple advertisers within one hour. A
-- time-range-only query can't split that traffic; content_id is the
-- minimum key that disambiguates which sessions actually saw which ad.

CREATE TABLE IF NOT EXISTS ad_content_map
(
    advertiser_id UInt64,
    content_id    UInt64,
    updated_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (advertiser_id, content_id);

-- Synthetic seed data. content_ids are real rows from ch_hackathon_content_data
-- (the raw content source table) so this maps onto content that actually has
-- session activity once content_dim/events_raw are populated from it.
-- Deliberately includes content_id 20971542 under two advertisers to model
-- the "same content, rotating sponsors within the hour" case.
INSERT INTO ad_content_map (advertiser_id, content_id) VALUES
    (1001, 20971521),
    (1001, 20971537),
    (1002, 20971538),
    (1002, 20971540),
    (1002, 20971542),
    (1003, 20971542),
    (1003, 20971543),
    (1004, 20971546);
