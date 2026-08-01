# LLD — What Runs When, Explained

Companion to `DESIGN_SIMPLE.md`. Written for someone comfortable with ClickHouse basics
(MergeTree, ORDER BY, materialized views) but not living in the docs.

Everything is organized by **when it runs**: Setup (once) → Insert (per batch of events)
→ Batch job (every 15–30s) → Query (per request).

---

## 0. Five ClickHouse facts this design leans on

If these five are clear, everything below is mechanical.

1. **A materialized view (MV) is an insert trigger, not a saved query.** When rows are
   inserted into table A, an MV on A runs its SELECT *on those new rows only* and appends
   the result to table B. It never re-reads A's history. Consequence: MVs are free
   incremental pipelines, but they only ever see rows *as inserted* — they cannot see
   later updates or dedups. This is why our dedup cannot rely on ReplacingMergeTree.

2. **A dictionary is an in-RAM hash map, refreshed in the background.** `CREATE DICTIONARY`
   loads `content_dim` into memory and re-reads it every LIFETIME seconds.
   `dictGetOrDefault(...)` is then a memory lookup — usable inside an MV or a batch INSERT
   at almost zero cost. This is how enrichment happens *without any query-time JOIN*.

3. **SummingMergeTree collapses duplicate keys eventually, not immediately.** Rows with
   the same ORDER BY key get their numeric columns added together *during background
   merges*, whenever those happen. Rule: never trust the collapse — always write queries
   as `sum(x) GROUP BY key`. Then the answer is right whether merges ran or not, and the
   engine's collapsing is purely a storage optimization.

4. **ReplacingMergeTree keeps the latest version eventually, not immediately.** Same idea:
   old versions linger until merges run. Rule: read with
   `argMax(column, ver) ... GROUP BY key` (cheap for a handful of keys), never rely on
   `FINAL` over a big table.

5. **ORDER BY is the index.** A query filtering on a *prefix* of the ORDER BY columns
   reads only the granules containing that prefix — a seek, not a scan. This single fact
   decides every ORDER BY below.

---

## 1. SETUP — run once

### 1.1 The raw event log

```sql
CREATE TABLE events_raw
(
    video_session_id  String,
    user_id           String,
    content_id        UInt64,
    event_type        LowCardinality(String),
    event             LowCardinality(String),
    event_ts          DateTime64(3, 'UTC'),
    platform          LowCardinality(String),
    app_version       LowCardinality(String),
    country           LowCardinality(String),
    audio_language    LowCardinality(String),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start     DateTime64(3, 'UTC'),
    ingest_ts         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toDate(session_start)
ORDER BY (video_session_id, event_ts);
```

In plain terms:
- **Append-only log. Nothing ever updates or deletes here.**
- `ingest_ts DEFAULT now64(3)`: the moment the row physically arrived. The source data
  has no arrival time, so we stamp our own. The batch job's cursor runs on this column,
  and "ingestion lag" (for ClickStack) is `now() − max(ingest_ts)`.
- `ORDER BY (session, ts)`: the batch job reads whole sessions → each session is one
  contiguous stretch on disk (fact #5).
- `PARTITION BY toDate(session_start)` — *not* event date. `session_start` is on every
  row and always correct, so a session's entire life lands in ONE partition even if it
  crosses midnight. Reprocessing a session never touches two partitions.

### 1.2 Content metadata + dictionary

```sql
CREATE TABLE content_dim
(
    content_id UInt64,
    title      String,
    video_type LowCardinality(String),
    category   LowCardinality(String),
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY content_id;

CREATE DICTIONARY content_dict
(
    content_id UInt64,
    title      String DEFAULT 'unknown',
    video_type String DEFAULT 'unknown',
    category   String DEFAULT 'unknown'
)
PRIMARY KEY content_id
SOURCE(CLICKHOUSE(TABLE 'content_dim'))
LAYOUT(HASHED())
LIFETIME(MIN 60 MAX 300);
```

In plain terms:
- Load the content CSV into `content_dim`. Reload any time — ReplacingMergeTree makes
  reloads idempotent (newest `updated_at` wins).
- The dictionary snapshots it into RAM and refreshes every 1–5 min (fact #2).
- **All enrichment everywhere is `dictGetOrDefault(content_dict, 'video_type',
  content_id, 'unknown')`.** The `OrDefault` matters: 1,089 content rows have blank
  metadata; a JOIN would silently drop those sessions from every video_type answer,
  the dictionary maps them to `'unknown'` instead.

### 1.3 One row per session: the switches

```sql
CREATE TABLE session_state
(
    video_session_id String,
    user_id          String,
    content_id       UInt64,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_type       LowCardinality(String),
    category         LowCardinality(String),
    fg               UInt8 DEFAULT 1,     -- foreground switch (default ON)
    playing          UInt8 DEFAULT 0,     -- playing switch    (default OFF)
    last_seen        DateTime64(3, 'UTC'),
    ended            UInt8 DEFAULT 0,
    open_run_start   Nullable(DateTime('UTC')),  -- +1 already written for this minute; NULL = no open run
    ver              UInt64                       -- max event_ts (ms) processed so far
)
ENGINE = ReplacingMergeTree(ver)
ORDER BY video_session_id
TTL toDateTime(last_seen) + INTERVAL 1 DAY;
```

In plain terms:
- This is the state machine's memory: the two switches, the clock, and whether a `+1`
  is currently "open" (written but not yet closed by a `−1`).
- "Update" = insert a new row with a higher `ver`; ReplacingMergeTree keeps the newest
  (fact #4). The batch reads it with `argMax(col, ver) GROUP BY video_session_id`,
  and only for the sessions present in the current batch — a point lookup, not a scan.
- Dimensions are copied in once, at the session's first event, and never changed —
  otherwise a mid-session platform drift (95 in the sample) would emit `+1` in one slice
  and `−1` in another, corrupting both forever.
- `ver` is also the late-event detector: an incoming event with `event_ts < ver` means
  "this lands inside history I already emitted" → retraction path (§3 step R).
- TTL is garbage collection for abandoned sessions. It has nothing to do with the 90s
  activity rule — deliberately separate timers.

### 1.4 The truth table: +1/−1 deltas

```sql
CREATE TABLE cc_delta_content
(
    minute         DateTime('UTC'),
    content_id     UInt64,
    platform       LowCardinality(String),
    country        LowCardinality(String),
    video_type     LowCardinality(String),
    category       LowCardinality(String),
    delta_sessions Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (content_id, platform, country, video_type, category, minute);
```

In plain terms:
- The only table the state machine writes concurrency into. Everything else derives
  from it automatically.
- SummingMergeTree because deltas are pure additions: retro-dated `−1`s, hour-cut
  `−1/+1` pairs, retraction negatives — all just add. Background merges squash rows
  sharing a key; queries do `sum(delta_sessions) GROUP BY minute` regardless (fact #3).
- `content_id` FIRST in ORDER BY: this table serves content drill-downs, whose filters
  always include content_id → prefix seek lands on ~5 rows (fact #5).

### 1.5 Auto-derived serving tables (MVs — they fire at Stage-2 insert time)

```sql
-- Narrow copy: what default dashboards read. Tiny (no content_id).
CREATE TABLE cc_delta_dims
(
    minute DateTime('UTC'),
    platform LowCardinality(String), country LowCardinality(String),
    video_type LowCardinality(String), category LowCardinality(String),
    delta_sessions Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (platform, country, video_type, category, minute);

CREATE MATERIALIZED VIEW cc_delta_dims_mv TO cc_delta_dims AS
SELECT minute, platform, country, video_type, category, delta_sessions
FROM cc_delta_content;
```

The MV runs at the instant the batch inserts into `cc_delta_content`, sees just those new
delta rows, and appends them minus `content_id` (fact #1). SummingMergeTree then collapses
the duplicates that creates. Dashboards get a table with tens of key-combos instead of
thousands. `platform` first because it's the dominant filter.

### 1.6 Session-INDEPENDENT view (fires at Stage-1 insert time — no state, no batch)

```sql
CREATE TABLE cc_si_minute
(
    minute DateTime('UTC'),
    content_id UInt64,
    platform LowCardinality(String), country LowCardinality(String),
    video_type LowCardinality(String),
    sessions_state AggregateFunction(uniqCombined(14), String),
    users_state    AggregateFunction(uniqCombined(14), String)
)
ENGINE = AggregatingMergeTree
PARTITION BY toDate(minute)
ORDER BY (platform, country, video_type, content_id, minute);

CREATE MATERIALIZED VIEW cc_si_minute_mv TO cc_si_minute AS
SELECT
    toStartOfMinute(event_ts) AS minute,
    content_id, platform, country,
    dictGetOrDefault(content_dict, 'video_type', content_id, 'unknown') AS video_type,
    uniqCombinedState(14)(video_session_id) AS sessions_state,
    uniqCombinedState(14)(user_id)          AS users_state
FROM events_raw
WHERE NOT (event = 'pause' OR event LIKE 'download%'
        OR event_type IN ('AppBackgrounded', 'VideoSessionEnd'))
GROUP BY minute, content_id, platform, country, video_type;
```

In plain terms:
- Definition: *a session counts in minute M if it emitted any non-inactive event in M.*
  No memory of previous minutes — that's what "session-independent" means.
- It fires on every insert into `events_raw`, enriches via the dictionary (a RAM lookup,
  fine inside an MV), and stores **uniq sketches**, not counts. Why sketches: a session
  emits ~1.5 events/min, so we must dedupe within the minute; and sketches from separate
  insert blocks *merge correctly* later — plain counts wouldn't (AggregatingMergeTree
  merges states; `uniqCombinedMerge` at query time finishes them).
- Known bias: reads ~9% high at peak (heartbeats fire from pockets and during pause).
  Not a bug — it's the stateless ceiling. Used as validation (SA ≤ SI), fallback, demo.

### 1.7 Bookkeeping: run ledger + cursor

```sql
CREATE TABLE session_runs
(
    video_session_id String,
    run_start DateTime('UTC'), run_end DateTime('UTC'),
    content_id UInt64,
    platform LowCardinality(String), country LowCardinality(String),
    video_type LowCardinality(String), category LowCardinality(String),
    sign Int8 DEFAULT 1                    -- -1 rows are retractions
)
ENGINE = MergeTree
PARTITION BY toDate(run_start)
ORDER BY (video_session_id, run_start);

CREATE TABLE pipeline_cursor
(
    name LowCardinality(String),
    cursor_ts DateTime64(3, 'UTC'),
    ver UInt64
)
ENGINE = ReplacingMergeTree(ver) ORDER BY name;
```

`session_runs` = every run ever emitted, per session — needed to *undo* a session exactly
(retraction), to backfill future tables, and as the audit trail for judges.
`pipeline_cursor` = one row: "I have processed events_raw up to ingest_ts X."

---

## 2. INSERT TIME — what happens when a batch of events lands

Suppose the replayer inserts 50,000 rows into `events_raw`. Synchronously, before the
insert returns:

1. Column defaults fire → every row gets `ingest_ts = now()`.
2. `cc_si_minute_mv` runs over exactly those 50,000 rows → enriches via `content_dict`
   (RAM), filters out pause/BG/download rows, groups per minute, appends sketch rows to
   `cc_si_minute`.
3. Done. **No other table is touched. Nothing is read. The session-aware path hasn't
   run yet** — it picks these rows up on its next tick via the cursor.

Insert hygiene at scale: batches ≥10K rows (or `async_insert=1`), and each batch carries
an `insert_deduplication_token` so a retried insert can't create duplicate raw rows.

---

## 3. BATCH TIME — the state machine, every 15–30s

The only component with logic. A thin scheduler runs these steps as ClickHouse SQL:

```
step 1  cursor := SELECT argMax(cursor_ts, ver) FROM pipeline_cursor
step 2  new    := SELECT ... FROM events_raw WHERE ingest_ts > cursor    -- new rows only
step 3  states := argMax-read session_state for JUST the session ids in `new`
step 4  fold   := per session: sort, dedupe, flip switches, detect run opens/closes
step 5  write  := deltas → cc_delta_content   (MVs fan out automatically)
                  closed runs → session_runs
                  new switch values → session_state (higher ver)
step 6  sweep  := sessions with an open run AND last_seen < now()−90s:
                  emit their −1, backdated to minute(last_seen + 90s); close run
step 7  advance cursor
```

### The fold (step 4), concretely

Per session, in one SQL statement (`groupArray` → `arraySort` → walk):

- **Dedupe**: drop exact repeats of `(event_type, event, event_ts)` — 4,210 duplicate
  rows exist in the sample; one duplicated boundary event would skew deltas forever.
- **Sort** by `(event_ts, tie_priority)` with fixed priority
  `START, PLAY, FG, RESUME, HB, ERR, PAUSE, BG, END`. Never trust arrival order (11.3%
  of steps arrive out of order) and never leave same-millisecond ties (161K of them) to
  chance.
- **Flip switches** per this mapping — each event touches ONE thing:

  | Event | Effect |
  |---|---|
  | `VideoPlay`, hb `resume` | `playing = 1` |
  | hb `pause` | `playing = 0` |
  | `AppForegrounded` | `fg = 1` |
  | `AppBackgrounded` | `fg = 0` |
  | `VideoSessionEnd` | `ended = 1` |
  | hb `download_*` | ignored completely |
  | `VideoError` and everything else (`buffer-health`, `Seek`, `BufferStart`…) | only `last_seen` |

  Every event also updates `last_seen`. **counted = fg AND playing AND NOT ended AND
  not-stale.** Note `AppForegrounded` does *not* set `playing` — return from background
  while paused stays uncounted until `resume`. The switch is the memory; we never store
  "last pause event".

- **Emit on change only**:
  - counted turns ON → insert `(+1, this minute)` into `cc_delta_content` *immediately*
    (eager, so open sessions count at the live edge); remember `open_run_start`.
  - counted turns OFF → insert `(−1, first minute after last counted one)`; append the
    run to `session_runs`; clear `open_run_start`.
  - still counted across an hour boundary → emit `−1` and `+1` both at the boundary
    (numbers unchanged; keeps every hour self-contained).

### Worked example

```
event                          fg  playing  counted   emitted
10:02:01 VideoSessionStart      1     0       no
10:02:03 VideoPlay              1     1       YES      +1 @ 10:02
10:05:30 hb pause               1     0       no       −1 @ 10:06   (run 10:02→10:06 → ledger)
10:06:10 AppBackgrounded        0     0       no
10:09:00 AppForegrounded        1     0       no                    ← back, still paused!
10:09:05 hb resume              1     1       YES      +1 @ 10:09
10:17:45 VideoSessionEnd        1     1→end   no       −1 @ 10:18   (run 10:09→10:18 → ledger)
```

Sixteen-minute session → four delta rows. The 40s heartbeats between these events emitted
nothing (84% of all heartbeats change no serving row — the whole scaling argument).

### Step R — retraction (rare; triggered by `event_ts < ver` in step 4)

A late event landing *inside* already-emitted history can't be folded forward. So, for
that one session only: (a) insert exact negatives of its ledger runs (sign=−1 rows +
inverse deltas — addition undoes addition), (b) re-fold the session from all its raw
events (one partition, one seek), (c) emit fresh. Cost bounded to one session.

**Idempotency:** re-running any batch is harmless — dedupe + the `ver` check mean
already-processed events emit nothing new.

---

## 4. QUERY TIME — what a dashboard request actually does

Dashboards never read `events_raw`. Every question becomes: *seek a short range of delta
rows, add them up in order.*

### 4.1 The curve (the building block)

```sql
SELECT minute,
       sum(d) OVER (ORDER BY minute) AS concurrency
FROM (
    SELECT minute, sum(delta_sessions) AS d          -- collapse unmerged parts (fact #3)
    FROM cc_delta_dims
    WHERE platform = 'ANDROID_PHONE'
      AND minute >= toStartOfHour(toDateTime('2026-07-26 10:30:00'))
      AND minute <  toDateTime('2026-07-26 11:30:00')
    GROUP BY minute
)
ORDER BY minute;
```

Why it starts at `toStartOfHour(from)`: hour cuts guarantee every session active at 10:00
re-emitted `+1` there, so the running sum starting at zero from the hour top is exact —
no need to read anything older, ever.

Mini numeric example — delta rows `(10:02,+1) (10:06,−1) (10:09,+1) (10:18,−1)` plus a
second session `(10:03,+1) (10:12,−1)`:

```
minute   10:02  10:03  10:06  10:09  10:12  10:18
delta     +1     +1     −1     +1     −1     −1
running    1      2      1      2      1      0
```

Between listed minutes the value simply holds (concurrency is a step function). We do
NOT generate the empty minutes — max and time-weighted avg don't need them:

### 4.2 Peak and average

```sql
WITH stepped AS (
    SELECT minute,
           sum(d) OVER (ORDER BY minute) AS cc,
           leadInFrame(minute, 1, toDateTime('2026-07-26 11:30:00'))
             OVER (ORDER BY minute ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) AS nxt
    FROM ( ...same inner query as 4.1... )
)
SELECT
    max(cc) AS peak,                                          -- exact: curve is flat between rows
    sum(cc * dateDiff('minute', greatest(minute, {from}), nxt))
      / sum(dateDiff('minute', greatest(minute, {from}), nxt)) AS avg_time_weighted
FROM stepped
WHERE nxt > {from};
```

Hour/day grain = `GROUP BY toStartOfHour(minute)` over `stepped` and take `max(cc)` per
bucket. Never average first and max later. Never sum two slices' peaks — run the query
with the exact filter asked. These ship as **parameterized views** so the dashboard and
LibreChat physically can't get the semantics wrong.

### 4.3 The other three reads

```sql
-- "Right now" (zero lag): count the open switches directly
SELECT count() FROM (
    SELECT argMax(fg,ver) fg, argMax(playing,ver) playing,
           argMax(ended,ver) ended, argMax(last_seen,ver) last_seen
    FROM session_state GROUP BY video_session_id )
WHERE fg=1 AND playing=1 AND ended=0 AND last_seen > now64(3) - INTERVAL 90 SECOND;

-- Peak PEOPLE (not streams): finish the sketches per minute, then max
SELECT max(u) FROM (
    SELECT minute, uniqCombinedMerge(users_state) AS u
    FROM cc_users_minute
    WHERE platform = {p} AND minute >= {from} AND minute < {to}
    GROUP BY minute );

-- Validation: session-aware must never exceed session-independent
-- (SA curve from 4.1) vs (SI curve below), alert if SA > SI * 1.02
SELECT minute, uniqCombinedMerge(sessions_state) AS si
FROM cc_si_minute
WHERE minute >= {from} AND minute < {to}
GROUP BY minute;
```

Freshness labelling: result rows with `minute > cursor − sweep interval` are
`provisional`; older rows are final. Same table, no seam.

---

## 5. Constants

| Constant | Value | Why |
|---|---|---|
| liveness timeout | 90s | ~2 missed 40s heartbeats (measured; docs' "60s" is wrong). Config — re-measure on unseen day |
| batch interval | 15–30s | live-edge freshness vs insert-parts pressure |
| insert batch size | ≥10K rows | avoid "too many parts" |
| state eviction TTL | 1 day | memory GC only; never affects the count |
| run cut | hour boundary | every hour self-contained; any run ≤60 min |
| serving grain | 1 minute | peak can't be rebuilt from coarser grains |
| foreground default | ON | 97% of first BG markers arrive after first PLAY |
| playing default | OFF | pre-`VideoPlay` = startup, not watching |
| tie-break priority | START,PLAY,FG,RESUME,HB,ERR,PAUSE,BG,END | reproducibility across 161K same-ms ties |
