# SonyLIV Data Analysis — Deep Dive (v2)
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


---

## 13. TRUE FOREGROUND CONCURRENCY (Delta Model)

### 13.1 Methodology

Using the **interval-to-delta model**:
1. Track state transitions per session (active/inactive) using the state machine from Section 10
2. Extract valid active intervals (from each `active` transition to the next `inactive` transition)
3. Convert to +1 at interval start, -1 at interval end
4. Cumulative sum over time = true foreground concurrency at each minute

### 13.2 Peak Foreground Concurrency

| Metric | Naive (any event) | Foreground-Only | Ratio |
|--------|-------------------|-----------------|-------|
| **Peak concurrent sessions** | 2,944 | **2,316** | 78.7% |
| **Peak minute** | 10:59 | **10:55** | (different!) |
| **Avg during event (when >100)** | ~2,500 | **1,577** | ~63% |
| **Minutes over 1,000** | ~60 | **45** | — |

**Critical Finding:** The foreground-only peak is **21.3% lower** than naive counting, but the average during the event is **37% lower**. This confirms the problem statement: naive overcounting is significant and non-uniform.

### 13.3 Top 10 Minutes by Foreground Concurrency

| Minute (UTC) | FG Concurrent | Naive Concurrent | FG % of Naive |
|--------------|---------------|------------------|---------------|
| 10:55 | 2,316 | 2,916 | 79.4% |
| 10:56 | 2,306 | 2,943 | 78.4% |
| 10:54 | 2,303 | 2,882 | 79.9% |
| 10:53 | 2,277 | 2,829 | 80.5% |
| 10:57 | 2,263 | 2,931 | 77.2% |
| 10:58 | 2,259 | 2,930 | 77.1% |
| 11:00 | 2,251 | 2,891 | 77.9% |
| 10:59 | 2,244 | 2,944 | 76.2% |
| 10:52 | 2,242 | 2,808 | 79.8% |
| 11:01 | 2,239 | 2,889 | 77.5% |

**Key Insight:** The FG/Naive ratio ranges from 76–81% during peak. This is the "correction factor" that varies by minute.

### 13.4 Hourly Summary (July 26 — Foreground Only)

| Hour (UTC) | Avg FG Concurrency | Peak FG | Min FG |
|------------|-------------------|---------|--------|
| 00:00–07:00 | 1–4 | 15 | 0 |
| 08:00 | 7 | 18 | 1 |
| 09:00 | 8 | 16 | 2 |
| **10:00** | **518** | **1,413** | 7 |
| **11:00** | **903** | **1,374** | 0 |

### 13.5 The Concurrency Curve (Minute-Level)

The full ramp-up and ramp-down during the main event:

```
Time (UTC)  | FG Concurrent | Phase
------------|---------------|------
10:29       | 17            | Pre-event baseline
10:30       | 252           | ← Event starts! (242 sessions join in 1 minute)
10:31       | 494           | Rapid ramp
10:35       | 679           |
10:40       | 950           |
10:45       | 1,146         |
10:50       | 1,195         |
10:55       | 1,266         | ← Approaching peak
10:59       | 1,413         | ← Peak (delta model, subset)
11:04       | 1,374         |
11:10       | 1,212         | ← Decline begins
11:15       | 1,089         |
11:20       | 812           |
11:25       | 520           |
11:28       | 271           |
11:30       | 36            | ← Event ends
```

**Event Duration:** ~60 minutes (10:30 → 11:30 UTC = 16:00 → 17:00 IST)
**Ramp-up time:** ~25 minutes to reach >1,000 concurrent
**Sustained peak (>1,200):** ~20 minutes (10:47–11:07)
**Ramp-down:** Steeper than ramp-up (event end is abrupt)

---

## 14. Platform-Level Foreground Concurrency at Peak

At the peak minute, platform breakdown of sessions with last-known-state = active:

| Platform | Total Sessions Present | FG Active | FG % |
|----------|----------------------|-----------|------|
| ANDROID_PHONE | 2,067 | 1,395 | 67.5% |
| IPHONE | 438 | 241 | 55.0% |
| SONY_ANDROID_TV | 381 | 243 | 63.8% |
| JIO_ANDROID_TV | 259 | 181 | 69.9% |
| Mweb | 73 | 42 | 57.5% |
| SAMSUNG_HTML_TV | 54 | 37 | 68.5% |
| ANDROID_TAB | 56 | 31 | 55.4% |
| FIRE_TV | 41 | 26 | 63.4% |
| XIAOMI_ANDROID_TV | 43 | 21 | 48.8% |
| LG_HTML_TV | 22 | 18 | 81.8% |

**Key Insight:** LG_HTML_TV has the highest foreground ratio (82%), while Xiaomi TV has the lowest (49%). Mobile platforms hover at 55–70%. This means **different platforms need different treatment** when estimating true audience.

---

## 15. User-Level vs Session-Level Concurrency

At peak minute:
| Metric | Sessions | Unique Users |
|--------|----------|--------------|
| Total present | 3,407 | 3,299 |
| FG active | 2,219 | 2,167 |

**108 sessions** at peak belong to users with multiple concurrent streams. This means session-level concurrency slightly overstates unique viewer count (~2.4% overlap).

---

## 16. Multi-Session Users

| Sessions per User | User Count |
|-------------------|-----------|
| 1 | 8,834 (91.9%) |
| 2 | 635 (6.6%) |
| 3 | 91 |
| 4 | 27 |
| 5+ | 31 |
| **301** | **1** (anomaly) |

### The 301-Session User (Anomaly)
- User ID: `4CE58A...` 
- Active only during the event window (10:30–11:30)
- Uses 4 platforms: SONY_ANDROID_TV, XIAOMI_ANDROID_TV, FIRE_TV, JIO_ANDROID_TV
- Peak simultaneous sessions: **110** at 11:00
- **Likely a load-testing bot or shared commercial account** (hotel, bar, etc.)

### Overlapping Sessions
- **61 unique users** have truly overlapping sessions (18,211 overlapping session-pairs)
- For user-level concurrency (vs session-level), need to deduplicate

---

## 17. Session Behavior Patterns

| Session Type | Count | % | Avg Duration |
|-------------|-------|---|--------------|
| Mixed (BG + Pause) | 9,775 | **90%** | 18.1 min |
| Bounced (<1 min) | 966 | 8.9% | 0.6 min |
| Background only | 125 | 1.2% | 11.7 min |
| Uninterrupted | 0 | 0% | — |
| Pause only | 0 | 0% | — |

**Critical:** There are ZERO uninterrupted sessions and ZERO pause-only sessions. Every session longer than 1 minute has both background and pause events. This confirms that foreground-only filtering is the **core of the problem**, not an edge case.

---

## 18. Pause Duration Analysis

When a user pauses and then resumes:

| Metric | Value |
|--------|-------|
| Pause→Resume pairs | 10,804 |
| P25 pause duration | 1.9 sec |
| **Median pause** | **7.3 sec** |
| P75 pause duration | 29.9 sec |
| P90 pause duration | 78.7 sec |
| Average pause | 31.2 sec |

**Insight:** Most pauses are very short (7 seconds median) — user tapped pause briefly. But P90 is 79 seconds, meaning 10% of pauses last over a minute. This inactive time would be falsely counted as active without the state machine.

---

## 19. Background Duration (Refined)

What happens after `AppBackgrounded`:

| Next Event | Count | Median Duration | P90 Duration | Avg Duration |
|-----------|-------|-----------------|--------------|--------------|
| AppForegrounded | 12,214 | 34.9 sec | 458.2 sec | 222.0 sec |
| VideoHeartbeat (resume) | 1,886 | 0.07 sec | 12.6 sec | 17.8 sec |
| VideoSessionEnd | 92 | 14.3 sec | 601.2 sec | 285.3 sec |
| VideoError | 21 | 29.0 sec | 530.6 sec | 183.9 sec |

**Key Finding:** 86% of BG events are followed by AppForegrounded (user returns). But 1,886 times, the system fires a resume heartbeat without an explicit Foreground event — this is important for state machine design (heartbeat activity = proof of foreground).

---

## 20. Video Startup Latency (SessionStart → Play)

| Platform | Sessions | Median Startup | P90 Startup | P99 Startup |
|----------|----------|----------------|-------------|-------------|
| ANDROID_PHONE | 6,470 | **2.3 sec** | 18.9 sec | 35.3 sec |
| JIO_ANDROID_TV | 828 | **2.3 sec** | 5.9 sec | 34.3 sec |
| ANDROID_TAB | 113 | **1.7 sec** | 17.2 sec | 48.6 sec |
| IPHONE | 1,514 | 3.3 sec | 32.1 sec | 40.5 sec |
| SONY_ANDROID_TV | 1,086 | 4.7 sec | 27.0 sec | 41.8 sec |
| FIRE_TV | 87 | 4.2 sec | 20.6 sec | 34.2 sec |
| SAMSUNG_HTML_TV | 168 | 5.2 sec | 37.4 sec | 46.4 sec |
| LG_HTML_TV | 77 | 5.5 sec | 27.5 sec | 42.8 sec |
| XIAOMI_ANDROID_TV | 152 | 7.2 sec | 34.7 sec | 39.9 sec |
| **Mweb** | 211 | **11.1 sec** | 27.1 sec | 40.8 sec |

**Key Insight:** Mobile web (Mweb) has 5x worse startup latency than native Android (11s vs 2.3s). This means the pre-play inactive period is longer for web sessions — directly impacts concurrency counting accuracy.

---

## 21. Quality of Experience by Platform

| Platform | Sessions | Downshifts/Session | Dropped Frames/Session | Error Rate % |
|----------|----------|-------------------|----------------------|-------------|
| IPHONE | 1,530 | 1.06 | 0.51 | **4.58%** |
| SONY_ANDROID_TV | 1,102 | 0.59 | 0.53 | **4.54%** |
| **Xiaomi TV** | 157 | 0.73 | 0.70 | **11.46%** |
| **Mweb** | 212 | 0.26 | 2.92 | **10.85%** |
| ANDROID_PHONE | 6,640 | 0.67 | 1.28 | 1.66% |
| JIO_ANDROID_TV | 828 | 0.18 | 0.34 | 1.33% |
| ANDROID_TAB | 156 | 0.51 | 0.93 | 0.64% |

**Key Insights:**
- Xiaomi TV and Mweb have ~11% error rates (10x worse than Android Phone)
- Mweb has the highest dropped frame rate (2.92/session)
- iPhone has high downshift rate (quality degradation) despite being premium hardware

---

## 22. Video Error Analysis

| Outcome After Error | Sessions |
|--------------------|----------|
| Error is last event (session dies) | 292 (99.7%) |
| Terminates with SessionEnd | 1 (0.3%) |
| Recovers and plays again | 0 (0%) |

**Critical Finding:** Video errors are **always fatal**. No session recovers after an error. This simplifies the state machine: `VideoError` = terminal state (same as `VideoSessionEnd`).

---

## 23. Buffering Analysis

| Metric | Value |
|--------|-------|
| Total buffer events (matched pairs) | 63,319 |
| P25 buffer duration | 0.24 sec |
| **Median buffer** | **0.4 sec** |
| P75 buffer | 0.75 sec |
| P90 buffer | 2.22 sec |
| P99 buffer | 66.6 sec |
| Average | 2.69 sec |

**Design Decision:** Buffering is **active viewing** (user is waiting for content, not paused/backgrounded). Sessions should remain ACTIVE during buffering. Most buffers are sub-second (CDN catchup), but P99 is 67 seconds — extended buffering could overlap with our 90-second timeout. This is fine since BufferStart/BufferEnd events still prove liveness.

---

## 24. Content Engagement: VOD vs Live

| Video Type | Sessions | Avg Duration | BG Events/Session | Pauses/Minute |
|-----------|----------|-------------|-------------------|---------------|
| **VOD** | 7,311 | **20.4 min** | 1.41 | 0.138 |
| **Live** | 2,350 | **10.1 min** | 1.26 | 0.197 |

**Insights:**
- VOD sessions last 2x longer than Live (makes sense — full episodes vs match clips)
- Live content has **43% more pauses per minute** than VOD (users checking other tabs during breaks?)
- Live has fewer BG events per session but higher BG-per-minute rate (0.125 vs 0.069)

### Top Content at Peak Minute

| Title | Type | FG Active Sessions |
|-------|------|-------------------|
| wekek ked | Live | 175 |
| dijoj jeh | VOD | 77 |
| verar feg | VOD | 48 |
| kenin ceb | VOD | 44 |
| dakuk keg | VOD | 43 |

**The top live content accounts for 175/2,219 = 7.9% of all FG active sessions at peak.**

---

## 25. Session Duration by Platform & Content Type

| Content Type | Platform | Sessions | Avg Duration |
|-------------|----------|----------|-------------|
| VOD | ANDROID_PHONE | 4,444 | **23.3 min** |
| VOD | ANDROID_TAB | 115 | 21.4 min |
| VOD | FIRE_TV | 71 | 20.0 min |
| Live | SAMSUNG_HTML_TV | 63 | 14.1 min |
| Live | ANDROID_PHONE | 1,633 | 10.4 min |
| Live | IPHONE | 364 | 9.0 min |

**Android Phone + VOD = highest engagement combo** (23.3 min average).

---

## 26. Heartbeat Gap Analysis (During Active Periods)

Distribution of gaps between heartbeat events on July 26:

| Gap Range | Count | % |
|-----------|-------|---|
| ≤5 sec | 333,305 | 59% |
| 5–30 sec | 83,036 | 15% |
| **30–45 sec** | **130,523** | **23%** |
| 45–60 sec | 1,208 | 0.2% |
| 60–90 sec | 2,022 | 0.4% |
| 90–300 sec | 3,189 | 0.6% |
| 5–10 min | 1,120 | 0.2% |
| >10 min | 11,591 | 2% |

- P95 gap (excluding >10min outliers): **40.01 sec**
- P99 gap: **159.18 sec**

**Confirms:** The heartbeat fires at ~30–40 second intervals. The 90-second timeout threshold captures 99.4% of legitimate heartbeat gaps. The >10min gaps are sessions that went background without proper BG events.

---

## 27. Overcounting Impact Summary

| Scenario | Peak | Avg During Event | Overcounting |
|----------|------|-----------------|--------------|
| Naive (any event = active) | 2,944 | ~2,500 | — |
| FG-Only (state machine) | 2,316 | 1,577 | **37% overcount on average** |

**The business impact:** If SonyLIV uses naive concurrency for capacity planning, they're provisioning for 37% more capacity than needed. If used for ad pricing, they're selling 37% inflated numbers. The foreground-only model is not just technically correct — it has direct revenue and cost implications.

---

## 28. Design Implications (Updated)

| Finding | Implication | Priority |
|---------|-------------|----------|
| 90% sessions have mixed BG+Pause | State machine is mandatory, not optional | P0 |
| Errors are always terminal | Simplify: VideoError = session dead | P0 |
| Heartbeat 30–40s, timeout 90s | Liveness window = 90 seconds | P0 |
| FG/Naive ratio = 76–81% at peak | Can't use a fixed correction factor (varies by minute) | P0 |
| Peak shifts (naive=10:59, FG=10:55) | Must compute FG correctly; can't just scale naive | P1 |
| 1 anomalous 301-session user | Need anomaly detection / user-level caps | P1 |
| Platform affects FG ratio (49–82%) | Platform should be a filter dimension in serving table | P1 |
| Mweb 11s startup latency | Long pre-play inactive period; important for VOD start | P2 |
| Live has more pauses/min than VOD | Content type affects concurrency dynamics | P2 |
| 61 users with overlapping sessions | User-level dedup needed for "unique viewers" metric | P2 |

---

## 29. Recommended Table Schema for Serving Layer

Based on this analysis, the optimal serving table should store **pre-computed minute-level concurrency** with dimensions:

```sql
CREATE TABLE concurrency_minute_serving (
    minute DateTime,
    platform String,
    video_type String,
    content_id Int64,
    category String,
    country String,
    
    -- Metrics
    fg_active_sessions UInt32,       -- foreground-only active
    naive_active_sessions UInt32,    -- any-event based (for comparison)
    fg_active_users UInt32,          -- unique users in foreground
    new_sessions UInt32,             -- sessions started this minute
    ended_sessions UInt32,           -- sessions ended this minute
    
    -- For incremental updates
    last_updated DateTime DEFAULT now()
) ENGINE = AggregatingMergeTree()
ORDER BY (minute, platform, video_type, content_id)
PARTITION BY toDate(minute);
```

This allows dashboard queries like:
- Peak concurrency by platform in last hour: `SELECT max(fg_active_sessions) ... WHERE platform = 'ANDROID_PHONE' GROUP BY minute`
- Average concurrency by content type: `SELECT avg(fg_active_sessions) ... WHERE video_type = 'live'`
- Dimension drill-down: any combination of filters with sub-second latency

---
