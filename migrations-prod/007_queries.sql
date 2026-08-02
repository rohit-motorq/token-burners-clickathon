-- Migration 007: Query patterns
--
-- Content dimensions (video_type, category, show_name) are NOT in fact_concurrency_deltas.
-- Look them up at query time via dict_content using content_id.
--
-- Event dimensions (platform, country, video_resolution) are direct columns.


-- ============================================================
-- PEAK ACTIVE CONCURRENCY (no filter)
-- ============================================================
-- SELECT max(concurrent) AS peak, argMax(minute, concurrent) AS peak_minute
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- )
-- WHERE minute >= '2026-07-26 10:00:00'
--   AND minute <  '2026-07-26 11:00:00';


-- ============================================================
-- FILTER BY CONTENT DIMENSION (via dictionary lookup)
-- ============================================================
-- SELECT max(concurrent) AS peak
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--           AND dictGet('dict_content', 'video_type', content_id) = 'live'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- );


-- ============================================================
-- FILTER BY EVENT DIMENSION (direct column)
-- ============================================================
-- SELECT max(concurrent) AS peak
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--           AND platform = 'ANDROID_PHONE'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- );


-- ============================================================
-- COMBINED (event dim + content dim)
-- ============================================================
-- SELECT max(concurrent) AS peak
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--           AND platform = 'ANDROID_PHONE'
--           AND dictGet('dict_content', 'video_type', content_id) = 'live'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- );


-- ============================================================
-- PEAK OPEN SESSIONS
-- ============================================================
-- SELECT max(concurrent) AS peak_open
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_open) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- );


-- ============================================================
-- CONCURRENCY CURVE (active + open side by side)
-- ============================================================
-- SELECT minute, active, open
-- FROM (
--     SELECT minute,
--            sum(da) OVER (ORDER BY minute) AS active,
--            sum(do) OVER (ORDER BY minute) AS open
--     FROM (
--         SELECT minute, sum(delta_sessions) AS da, sum(delta_open) AS do
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- )
-- WHERE minute >= '2026-07-26 10:00:00'
--   AND minute <  '2026-07-26 12:00:00'
-- ORDER BY minute;


-- ============================================================
-- PER-PLATFORM PEAKS
-- ============================================================
-- SELECT platform, max(concurrent) AS peak, argMax(minute, concurrent) AS peak_at
-- FROM (
--     SELECT platform, minute,
--            sum(d) OVER (PARTITION BY platform ORDER BY minute) AS concurrent
--     FROM (
--         SELECT platform, minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--         GROUP BY platform, minute
--         ORDER BY platform, minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- )
-- GROUP BY platform
-- ORDER BY peak DESC;


-- ============================================================
-- UNIQUE ACTIVE USERS (from fact_concurrency_stats)
-- ============================================================
-- SELECT
--     uniqMerge(active_users) AS unique_active_users,
--     uniqMerge(open_users) AS unique_open_users
-- FROM fact_concurrency_stats FINAL
-- WHERE toDate(minute) = '2026-07-26'
--   AND minute >= '2026-07-26 10:00:00'
--   AND minute <  '2026-07-26 11:00:00';


-- ============================================================
-- AVERAGE CONCURRENCY (occupied minutes)
-- ============================================================
-- SELECT round(avg(concurrent), 2) AS avg_concurrent
-- FROM (
--     SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
--     FROM (
--         SELECT minute, sum(delta_sessions) AS d
--         FROM fact_concurrency_deltas FINAL
--         WHERE toDate(minute) = '2026-07-26'
--         GROUP BY minute
--         ORDER BY minute WITH FILL
--             FROM toDateTime('2026-07-26 00:00:00')
--             TO   toDateTime('2026-07-27 00:00:00')
--             STEP INTERVAL 1 MINUTE
--     )
-- )
-- WHERE concurrent > 0;


-- ============================================================
-- VERIFICATION
-- ============================================================
-- Net delta should be 0 (all sessions closed):
--   SELECT sum(delta_sessions) FROM fact_concurrency_deltas FINAL;
--
-- Concurrency never negative:
--   SELECT min(concurrent) FROM (
--       SELECT sum(d) OVER (ORDER BY minute) AS concurrent
--       FROM (SELECT minute, sum(delta_sessions) AS d FROM fact_concurrency_deltas FINAL
--             GROUP BY minute ORDER BY minute WITH FILL FROM ... TO ... STEP INTERVAL 1 MINUTE)
--   );
