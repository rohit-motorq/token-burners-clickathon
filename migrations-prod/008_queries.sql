-- Migration 008: Submission queries (matches SONYLIV_SUBMISSION_GUIDELINES.md)
--
-- Database: rohitdevtestingv8
-- Tables: fact_concurrency_deltas, fact_concurrency_stats, dict_content
--
-- NOTE: '2026-07-31' is the unseen event day. Replace with any date for other days.
-- In a dashboard UI, this would be a parameter: {date:Date}
--
-- Dimensions available for filtering:
--   Direct columns: platform, country, video_resolution
--   Via dict_content: video_type, category, show_name, title
--   Via content_id: any content-level filter


-- ============================================================
-- 1. CONCURRENCY CURVE — full event window (mandatory)
-- Shows concurrent viewers over time with visible peak + ramp
-- ============================================================

-- 1a. Minute-level curve for the main event day
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 1b. Peak + average for the event window
SELECT
    max(concurrent) AS peak_concurrent_viewers,
    argMax(minute, concurrent) AS peak_minute,
    round(avgIf(concurrent, concurrent > 0), 0) AS avg_concurrent,
    countIf(concurrent > 0) AS occupied_minutes
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
);


-- ============================================================
-- 2. DATASET FILTERS (mandatory) — applied to concurrency curve
-- ============================================================

-- 2a. Filter by PLATFORM (direct column)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND platform = 'ANDROID_PHONE'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2b. Filter by COUNTRY (direct column)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND country = 'india'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2c. Filter by VIDEO_TYPE (via dictionary lookup on content_id)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND dictGet('dict_content', 'video_type', content_id) = 'live'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2d. Filter by SHOW_NAME (via dictionary)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND dictGet('dict_content', 'show_name', content_id) = 'bgfjb'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2e. Filter by CATEGORY (via dictionary)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND dictGet('dict_content', 'category', content_id) = 'bffff'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2f. Filter by VIDEO_RESOLUTION (direct column)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND video_resolution = '1080p'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2g. Filter by CONTENT TITLE (via dictionary)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND dictGet('dict_content', 'title', content_id) = 'jipep dih'  -- filter
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;

-- 2h. COMBINED FILTER (platform + video_type)
SELECT minute, concurrent AS concurrent_viewers
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
          AND platform = 'ANDROID_PHONE'
          AND dictGet('dict_content', 'video_type', content_id) = 'live'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
ORDER BY minute;


-- ============================================================
-- 3. BREAKDOWNS (per-dimension peaks)
-- ============================================================

-- 3a. Peak per platform
SELECT platform, max(concurrent) AS peak, argMax(minute, concurrent) AS peak_at
FROM (
    SELECT platform, minute,
           sum(d) OVER (PARTITION BY platform ORDER BY minute) AS concurrent
    FROM (
        SELECT platform, minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY platform, minute
        ORDER BY platform, minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
GROUP BY platform
ORDER BY peak DESC;

-- 3b. Peak per video_type (via dict)
SELECT
    dictGet('dict_content', 'video_type', content_id) AS video_type,
    max(concurrent) AS peak
FROM (
    SELECT content_id, minute,
           sum(d) OVER (PARTITION BY content_id ORDER BY minute) AS concurrent
    FROM (
        SELECT content_id, minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY content_id, minute
    )
)
GROUP BY video_type
ORDER BY peak DESC;

-- 3c. Peak per country
SELECT country, max(concurrent) AS peak, argMax(minute, concurrent) AS peak_at
FROM (
    SELECT country, minute,
           sum(d) OVER (PARTITION BY country ORDER BY minute) AS concurrent
    FROM (
        SELECT country, minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY country, minute
        ORDER BY country, minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
GROUP BY country
ORDER BY peak DESC;


-- ============================================================
-- 4. OPEN SESSIONS (active vs total open)
-- ============================================================

-- Active vs open curve (shows % truly watching)
SELECT minute, active, open,
       round(active / greatest(open, 1) * 100, 1) AS pct_watching
FROM (
    SELECT minute,
           sum(da) OVER (ORDER BY minute) AS active,
           sum(do) OVER (ORDER BY minute) AS open
    FROM (
        SELECT minute, sum(delta_sessions) AS da, sum(delta_open) AS do
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE active > 0 OR open > 0
ORDER BY minute;


-- ============================================================
-- 5. UNIQUE USERS (from fact_concurrency_stats)
-- ============================================================

-- Unique active users in a time window
SELECT
    uniqMerge(active_users) AS unique_active_users,
    uniqMerge(open_users) AS unique_open_users,
    max(active_sessions) AS peak_active_sessions,
    max(open_sessions) AS peak_open_sessions
FROM fact_concurrency_stats FINAL
WHERE toDate(minute) = '2026-07-31'
  AND minute >= '2026-07-31 10:00:00'
  AND minute <  '2026-07-31 12:00:00';


-- ============================================================
-- 6. HOURLY SUMMARY
-- ============================================================

SELECT
    toStartOfHour(minute) AS hour,
    max(concurrent) AS peak,
    round(avg(concurrent), 0) AS avg_concurrent
FROM (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM fact_concurrency_deltas FINAL
        WHERE toDate(minute) = '2026-07-31'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-31 00:00:00')
            TO   toDateTime('2026-08-01 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
WHERE concurrent > 0
GROUP BY hour
ORDER BY hour;


-- ============================================================
-- FILTER → DIMENSION MAPPING (for README documentation)
-- ============================================================
-- | UI Filter        | Source                                          |
-- |------------------|-------------------------------------------------|
-- | Platform         | fact_concurrency_deltas.platform (direct)       |
-- | Country/Region   | fact_concurrency_deltas.country (direct)        |
-- | Video Resolution | fact_concurrency_deltas.video_resolution (direct)|
-- | Content Title    | dictGet('dict_content','title',content_id)      |
-- | Video Type       | dictGet('dict_content','video_type',content_id) |
-- | Category         | dictGet('dict_content','category',content_id)   |
-- | Show Name        | dictGet('dict_content','show_name',content_id)  |
-- | Date             | toDate(minute) partition filter                 |
-- | Time Range       | minute >= start AND minute < end (output filter)|
