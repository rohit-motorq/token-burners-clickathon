# SonyLIV Data Analysis — Deep Dive
## Click-a-thon 2026 · Foreground-Only Concurrency

---

## 1. Dataset Overview

| Metric | Value |
|--------|-------|
| **Raw events table** | `ch_hackathon_raw_data` |
| **Content metadata table** | `ch_hackathon_content_data` |
| **Total raw events** | 905,558 |
| **Total content entries** | 33,464 |
| **Unique sessions** | 10,866 |
| **Unique users** | 9,618 |
| **Content IDs actually watched** | 3,357 (out of 33,464) |
| **Date range** | 2026-07-14 to 2026-07-26 |
| **Country** | India (100% of data) |

```sql
-- Query: Basic counts
SELECT count() FROM ch_hackathon_raw_data;  -- 905,558
SELECT count() FROM ch_hackathon_content_data;  -- 33,464
SELECT count(DISTINCT video_session_id) FROM ch_hackathon_raw_data;  -- 10,866
SELECT count(DISTINCT user_id) FROM ch_hackathon_raw_data;  -- 9,618
```

---

## 2. Temporal Distribution

### 2.1 Events by Date

The data is **heavily concentrated on July 26, 2026** — this is the primary day of activity.

| Date | Events | Sessions | Users |
|------|--------|----------|-------|
| 2026-07-14 | 152 | 2 | 1 |
| 2026-07-21 | 65 | 2 | 2 |
| 2026-07-22 | 6,025 | 25 | 16 |
| 2026-07-23 | 8,195 | 49 | 36 |
| 2026-07-24 | 11,136 | 71 | 52 |
| 2026-07-25 | 30,097 | 204 | 137 |
| **2026-07-26** | **849,888** | **10,524** | **9,455** |

**Key Insight:** 93.8% of all events and 96.8% of all sessions are on July 26. This is likely a live sport event day (high simultaneous viewership).

```sql
SELECT toDate(fromUnixTimestamp64Milli(toInt64(event_timestamp))) as event_date,
       count() as events, count(DISTINCT video_session_id) as sessions, count(DISTINCT user_id) as users
FROM ch_hackathon_raw_data GROUP BY event_date ORDER BY event_date;
```

### 2.2 Hourly Distribution (UTC)

| Hour (UTC) | Events | Sessions |
|------------|--------|----------|
| 10 | 427,308 | 6,834 |
| 11 | 378,073 | 6,994 |
| 8 | 14,186 | 139 |
| 9 | 19,781 | 167 |

**Key Insight:** The peak activity is between 10:00–11:59 UTC (15:30–17:29 IST), which aligns with prime-time or live sport hours in India.

### 2.3 Peak Concurrency Minutes (Naive — All Sessions)

| Minute | Distinct Sessions Active |
|--------|--------------------------|
| 2026-07-26 10:59 | 2,944 |
| 2026-07-26 10:56 | 2,943 |
| 2026-07-26 10:57 | 2,931 |
| 2026-07-26 10:58 | 2,930 |
| 2026-07-26 10:55 | 2,916 |
| 2026-07-26 11:00 | 2,891 |

**This is the NAIVE count** (any session emitting any event in that minute). The foreground-only count will be lower after excluding backgrounded/paused periods.

```sql
SELECT toStartOfMinute(fromUnixTimestamp64Milli(toInt64(event_timestamp))) as minute,
       count(DISTINCT video_session_id) as active_sessions
FROM ch_hackathon_raw_data GROUP BY minute ORDER BY active_sessions DESC LIMIT 10;
```

---

## 3. Event Type & Event Breakdown

### 3.1 Event Types (7 distinct)

| event_type | Count | Description |
|------------|-------|-------------|
| `VideoHeartbeat` | 843,721 | Periodic signals (every ~30s) |
| `AppBackgrounded` | 14,700 | User left the app |
| `AppForegrounded` | 14,321 | User returned to the app |
| `VideoPlay` | 10,883 | Playback started |
| `VideoSessionEnd` | 10,881 | Session terminated |
| `VideoSessionStart` | 10,880 | Session created |
| `VideoError` | 293 | Playback error |

### 3.2 Heartbeat Sub-Events (Top Events Under `VideoHeartbeat`)

| Event | Count | Meaning |
|-------|-------|---------|
| `network-activity` | 177,485 | Network I/O happening |
| `buffer-health` | 167,460 | Buffer status report |
| `video-resize` | 141,250 | Video frame resize |
| `BufferStart` | 66,641 | Buffering began |
| `BufferEnd` | 66,289 | Buffering ended |
| `video_forward` | 49,879 | Fast forward |
| `Seek` | 32,036 | User seeked |
| `resume` | 31,780 | Playback resumed |
| `network-bandwidth` | 30,637 | Bandwidth measurement |
| `pause` | 27,340 | User paused |
| `upshift` | 19,400 | Quality increased |
| `dropped-frames` | 11,089 | Frame drops |
| `downshift` | 7,294 | Quality decreased |

**Key Insight:** The "periodic heartbeat" signals are `buffer-health`, `video-resize`, and `network-activity` — they fire every ~30 seconds and prove the session is alive and in the foreground.

```sql
SELECT event_type, event, count() as cnt
FROM ch_hackathon_raw_data GROUP BY event_type, event ORDER BY event_type, cnt DESC;
```

---

## 4. Session Characteristics

### 4.1 Session Duration Distribution

| Percentile | Duration (minutes) |
|------------|-------------------|
| P25 | 3.89 |
| **Median** | **11.95** |
| P75 | 23.20 |
| P90 | 33.35 |
| P95 | 41.59 |
| P99 | 73.89 |
| Max | 2,618.35 (~43 hours) |
| **Average** | **16.44** |

```sql
SELECT quantile(0.5)((max(event_timestamp) - min(event_timestamp)) / 60000.0) as median_duration_min
FROM ch_hackathon_raw_data GROUP BY video_session_id;
```

### 4.2 Events Per Session

| Percentile | Events |
|------------|--------|
| P25 | 21 |
| Median | 54 |
| P75 | 121 |
| P90 | 181 |
| P99 | 436 |
| Max | 1,803 |
| Average | 83.3 |

### 4.3 Session-Content Relationship

- **99.99% of sessions** have exactly 1 content_id (10,865 out of 10,866)
- Only 1 session spans 2 content_ids
- **Each session = one piece of content being watched**

### 4.4 Open Sessions

```sql
SELECT count(DISTINCT video_session_id) as open_sessions
FROM ch_hackathon_raw_data
WHERE video_session_id NOT IN (
    SELECT video_session_id FROM ch_hackathon_raw_data WHERE event_type = 'VideoSessionEnd'
);
-- Result: 0 (all sessions are closed in this dataset)
```

**Note:** The unseen day data may contain open sessions. Our pipeline must handle them.

---

## 5. Heartbeat & Gap Analysis

### 5.1 Periodic Heartbeat Interval

The periodic signals (`buffer-health`, `video-resize`, `network-activity`) fire at consistent intervals:

| Percentile | Gap (seconds) |
|------------|---------------|
| P25 | 30.15 |
| **Median** | **40.00** |
| P75 | 40.00 |
| P90 | 40.01 |
| P95 | 40.02 |

**Key Finding:** The heartbeat interval is ~30–40 seconds, NOT 1 minute as stated in docs. This is critical for setting our "liveness timeout" threshold.

### 5.2 Gap Between Any Consecutive Events in a Session

| Percentile | Gap (seconds) |
|------------|---------------|
| P25 | — |
| Median | 1.03 |
| P75 | 22.03 |
| P90 | 40.00 |
| P95 | 40.00 |
| P99 | 61.62 |

**Chosen Timeout Threshold: 90 seconds.** If no event arrives within 90 seconds (2.25x the normal heartbeat interval), the session is considered inactive/dead. This gives ample buffer for network jitter while catching truly dead sessions.

```sql
-- Periodic heartbeat gaps
SELECT quantile(0.5)(gap_sec) as median_hb_gap
FROM (
    SELECT (event_timestamp - lagInFrame(event_timestamp) 
            OVER (PARTITION BY video_session_id ORDER BY event_timestamp)) / 1000.0 as gap_sec
    FROM ch_hackathon_raw_data
    WHERE event IN ('buffer-health', 'video-resize', 'network-activity')
) WHERE gap_sec > 1 AND gap_sec < 300;
```

---

## 6. Background/Foreground Behavior

### 6.1 Key Stats

| Metric | Value |
|--------|-------|
| Sessions with AppBackgrounded | **10,866 (100%)** |
| Sessions with pause events | **10,669 (98.2%)** |
| Avg background events per session | 1.35 |
| Avg foreground events per session | 1.32 |
| Avg pause events per session | 2.52 |
| Avg resume events per session | 2.92 |

**Critical Insight:** EVERY session in this dataset has at least one AppBackgrounded event. This means background exclusion is not edge-case handling — it's the core of the problem. Without it, you're overcounting on literally every session.

### 6.2 Background Duration (time between AppBackgrounded → AppForegrounded)

| Percentile | Duration (seconds) |
|------------|-------------------|
| P25 | 5.3 |
| **Median** | **36.2** |
| P75 | 158.3 |
| P90 | 513.0 |
| P99 | 2,106.3 |
| **Average** | **230.1** |

**Key Insight:** The median background duration is 36 seconds, but the average is 230 seconds (nearly 4 minutes). The long tail (P99 = 35 minutes) means some users leave the app for extended periods. Counting this time as "active" would massively overstate concurrency.

---

## 7. Platform Distribution

| Platform | Events | Sessions | Users |
|----------|--------|----------|-------|
| ANDROID_PHONE | 629,646 | 6,640 | 5,954 |
| SONY_ANDROID_TV | 79,850 | 1,102 | 846 |
| IPHONE | 78,020 | 1,530 | 1,346 |
| JIO_ANDROID_TV | 56,567 | 828 | 810 |
| Mweb | 16,166 | 212 | 190 |
| ANDROID_TAB | 13,021 | 156 | 140 |
| XIAOMI_ANDROID_TV | 10,322 | 157 | 113 |
| SAMSUNG_HTML_TV | 9,969 | 168 | 159 |
| FIRE_TV | 7,260 | 91 | 78 |
| LG_HTML_TV | 4,737 | 77 | 74 |

**Key Insight:** Android Phone dominates (61% of sessions). Smart TVs collectively account for ~22% of sessions. Mobile (Phone + Tab + iPhone) = ~77%.

---

## 8. Content Distribution

### 8.1 Video Type (VOD vs Live)

| Video Type | Sessions | Events |
|------------|----------|--------|
| VOD | 7,971 | 778,455 |
| Live | 2,646 | 101,293 |
| (empty) | 250 | 25,810 |

**73% VOD, 24% Live, 3% unmapped.**

### 8.2 Category Distribution

Categories are obfuscated (e.g., `bhdbj`, `dcchh`, `dgddd`). There are **80 distinct categories**, roughly evenly distributed (~415–431 content items each). This means categories are well-balanced for testing dimension filters.

### 8.3 Audio Language

| Language | Sessions |
|----------|----------|
| hin (Hindi) | 6,313 |
| eng (English) | 2,116 |
| unk (Unknown) | 6,675 |
| HIN | 1,429 |
| mal (Malayalam) | 228 |
| tel (Telugu) | 86 |

---

## 9. App Version Distribution

| App Version | Sessions |
|-------------|----------|
| 6.34.8 | 5,225 |
| 6.25.1 | 1,030 |
| 8.9.5 | 988 |
| 6.34.4 | 872 |
| 3.11.1 | 646 |

---

## 10. Active vs Inactive — The State Machine

Based on the above analysis, here is the definitive **active/inactive classification**:

### Signals That Make a Session ACTIVE

| Signal | Condition |
|--------|-----------|
| `VideoPlay` (event=Play) | Playback has started |
| `resume` heartbeat | User unpaused |
| Any `VideoHeartbeat` (non-pause) while already active | Extends liveness |
| `AppForegrounded` + subsequent `resume` | Returned and resumed |

### Signals That Make a Session INACTIVE

| Signal | Condition |
|--------|-----------|
| `AppBackgrounded` | User left the app |
| `pause` heartbeat | User explicitly paused |
| Heartbeat gap > 90 seconds | No proof of life |
| `VideoSessionEnd` | Session terminated |
| `VideoError` | Playback failed |
| Before `VideoPlay` | Session created but not yet playing |

### Important Edge Cases

1. **`AppForegrounded` alone does NOT make a session active.** The user returned to the app, but if they were paused before backgrounding, they're still paused. A `resume` event must follow.

2. **`pause` within heartbeats is a sub-event of `VideoHeartbeat`.** The event_type is still `VideoHeartbeat`, but the event field is `pause`. Must check both fields.

3. **BufferStart/BufferEnd** — buffering is still active viewing (the user is waiting for content). Do NOT mark as inactive during buffering.

4. **Seek/video_forward/video_rewind** — these are user interactions, proof of active engagement.

### Example Session Timeline

```
06:20:56 VideoSessionStart       → INACTIVE (not yet playing)
06:20:58 VideoPlay/Play          → ACTIVE ✓
06:22:27 VideoHeartbeat/pause    → INACTIVE (paused)
06:22:43 AppBackgrounded         → INACTIVE (backgrounded)
06:22:59 AppForegrounded         → INACTIVE (still paused)
06:23:02 VideoHeartbeat/resume   → ACTIVE ✓
06:24:08 VideoHeartbeat/pause    → INACTIVE
06:24:09 AppBackgrounded         → INACTIVE
06:24:40 AppForegrounded         → INACTIVE (paused before BG)
06:24:40 VideoHeartbeat/resume   → ACTIVE ✓
06:31:45 AppBackgrounded         → INACTIVE
06:36:11 AppForegrounded         → INACTIVE (3.7 min gap)
06:36:15 VideoHeartbeat/resume   → ACTIVE ✓
06:36:58 VideoHeartbeat/pause    → INACTIVE
06:36:59 AppBackgrounded         → INACTIVE
06:41:51 AppForegrounded         → INACTIVE
06:41:51 VideoHeartbeat/resume   → ACTIVE ✓
06:41:54 VideoHeartbeat/pause    → INACTIVE
06:41:59 VideoSessionEnd         → INACTIVE (terminal)
```

**Active minutes in this 21-minute session:** Only about 12 minutes were truly active. The naive approach would count all 21 minutes.

---

## 11. Key Design Implications

| Finding | Implication for Solution |
|---------|--------------------------|
| 100% sessions have BG events | Background exclusion is essential, not optional |
| Heartbeat interval = 30–40s | Timeout threshold should be ~90s (2–3x) |
| Median BG duration = 36s | Short BG periods are common; must handle precisely |
| 93.8% data on one day | The "unseen day" will likely be similarly concentrated |
| Peak naive concurrency ~2,944 | True foreground concurrency will be significantly lower |
| All sessions closed | Unseen data may have open sessions; build for incremental updates |
| 1 content per session | No need to handle mid-session content switches |
| 10 platforms, 80 categories | Multi-dimensional filtering is required |

---

## 12. Queries Used for This Analysis

All queries were run against:
```
Host: mg6ws6jmpr.ap-south-1.aws.clickhouse.cloud:8443
User: default
```

Full query set available in the sections above, inline with each metric.
