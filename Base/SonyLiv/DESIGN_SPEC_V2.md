# Design Spec v2 — Foreground-Only Concurrency at 100x
## Staff review of `DESIGN.md`, with revised architecture, DDL, edge cases, and mentor questions

Baseline: `DESIGN.md` (3-gate model, hybrid hot/cold delta, hour-cut runs). The gate logic and the
two delta-bug fixes are correct and proven against brute force — **keep them**. The pipeline
topology, table layout, and query strategy need to change to survive 100x and to satisfy the
hard requirement that *all concurrency computation lives in ClickHouse*.

---

# 1. Design Spec

## 1.1 High-level design

Scale framing at 100x: ~90M events/day, ~1.1M sessions/day, peak concurrency ~300K, delta rows
~5M/day at content grain. ClickHouse eats this for breakfast **if** heartbeat volume never becomes
serving-row volume. The load-bearing measurement from the EDA: **84% of heartbeats change no
serving row** (same minute as previous signal). The entire design exists to preserve that property.

```
CLIENTS ── ~40s heartbeats
   │
   ▼
GATEWAY / KAFKA (buffering + batching only; NO logic here)
   │  batched inserts, insert_deduplication_token per batch
   ▼
┌─────────────────────────── CLICKHOUSE ───────────────────────────────┐
│  events_raw (MergeTree, append-only, ingest_ts stamped)              │
│      │                                                               │
│      │  MICRO-BATCH STATE MACHINE — pure ClickHouse SQL,             │
│      │  driven by a thin scheduler every 15–30s:                     │
│      │   1. read events with ingest_ts > cursor                      │
│      │   2. join session_state, sort by event_ts + tie priority      │
│      │   3. run the 3 gates, cut runs at hour boundaries             │
│      │   4. emit +1/−1 into cc_delta_content (eager +1 at run open)  │
│      │   5. emit −1 for sessions stale past 90s liveness             │
│      │   6. upsert session_state (ReplacingMergeTree, versioned)     │
│      │                                                               │
│      ▼                                                               │
│  cc_delta_content (SummingMergeTree)  ← single source of truth       │
│      │ MV cascade                                                    │
│      ├─► cc_delta_dims (SummingMergeTree, narrow, dashboard default) │
│      └─► cc_users_minute (AggregatingMergeTree, uniq sketches)       │
│                                                                      │
│  QUERY LAYER: sparse cumulative sum from hour boundary               │
│   peak = max over sparse points · avg = duration-weighted            │
│   rows with minute > watermark labelled 'provisional'                │
└──────────────────────────────────────────────────────────────────────┘
   │
   ▼
DASHBOARD / LibreChat+MCP · ClickStack observes cursor lag & query latency
```

**Opinionated departure #1 from `DESIGN.md`: no Flink.** The problem statement requires
ingestion, modeling, and all concurrency computation in ClickHouse. `DESIGN.md`'s Flink state
machine violates that requirement, adds an ops surface the hackathon cannot afford, and — the real
point — is unnecessary: the state machine is a per-session fold over time-ordered events, which
ClickHouse expresses natively with `arraySort` + `arrayFold`/window functions over micro-batches,
with `session_state` as the checkpoint store. Kafka survives only as a dumb buffer (and for the
hackathon, a CSV replayer doing batched inserts is equivalent evidence).

**Opinionated departure #2: hour-cut runs make anchors redundant — delete the anchors.**
`DESIGN.md` carries both hourly absolute anchors *and* hour-boundary run cuts. Cutting every run
at hour boundaries means every session active across an hour boundary re-emits `+1` at the top of
the hour, so **each hour's deltas are self-contained: cumulative sum from any hour start begins at
zero and is exact**. Any query reads at most 60 minutes of deltas before its window. Anchors are a
second mechanism solving the same problem, with a worse failure mode (they are absolute snapshots
that must be invalidated and recomputed on late data; deltas are additive and never need repair).
One mechanism, kept honest by constant use.

**Opinionated departure #3: never densify the minute axis.** `DESIGN.md` fixes Bug 2 with
`WITH FILL`. Correct, but wasteful and fragile at scale (fill interacts badly with multi-group
queries, and a day-grain query over 30 days materializes 43K minutes per combo). Concurrency is a
step function — constant between deltas — so:
- **Peak** = `max` of the running sum evaluated *only at delta points* (plus the value entering
  the window). Exact, no fill.
- **Average (time-weighted)** = each running-sum value weighted by minutes until the next delta,
  clamped to the window. Exact, no fill.
- **Average over occupied minutes** = same weights restricted to value > 0.
Fill is only ever needed to *render* a chart, and even then the frontend can step-interpolate.

## 1.2 Full pipeline, ingestion → visualization

1. **Ingest.** Batched inserts (10K–500K rows) into `events_raw`. `ingest_ts DEFAULT now64(3)` on
   every row — the EDA proved lateness is unobservable in the source, so the pipeline must stamp
   it itself. This is also the ClickStack signal (ingest lag = `ingest_ts − event_ts`).
   Normalization at ingest via `MATERIALIZED` columns: lowercase languages, collapse
   `unk/UNK/''/OFF` sentinels to `'unknown'`. Transport-level duplicate protection via
   `insert_deduplication_token`.
2. **Enrichment.** Content metadata as a **dictionary**, not a join table.
   `dictGetOrDefault(content_dict, 'video_type', content_id, 'unknown')` gives LEFT-JOIN +
   coalesce semantics by construction — the EDA showed an INNER join silently deletes 250 sessions
   (1,089 blank-metadata content rows) from every `video_type` answer. At 100x (3.3M titles) a
   `HASHED` layout is still ~hundreds of MB; move to `CACHE` layout only if it isn't.
3. **State machine (micro-batch, every 15–30s).** For each session with new events: load prior
   `session_state`, append new events, `arraySort` by `(event_ts, tie_priority)` with the fixed
   priority `START, PLAY, FG, RESUME, HB, ERR, PAUSE, BG, END` (161,660 same-ms collisions make
   this mandatory for reproducibility), dedupe exact `(session, type, event, ts)` repeats, fold the
   three gates (foreground default ON, playing default OFF, 90s liveness), cut at hour boundaries,
   diff against `emitted_until` to emit only *new* deltas. Eager `+1` at run open; the closing `−1`
   comes either from an explicit inactive transition or from the staleness sweep (step 5 in the
   diagram), retro-dated to `last_signal_minute + 90s` — the additive model absorbs retro-dated
   rows with zero rework.
4. **Serving cascade.** `cc_delta_content` is the only table the state machine writes. Materialized
   views fan out to the narrow table and the user-sketch table on insert. Adding a dimension later
   = one new MV + backfill from `session_runs`; no pipeline change (this answers the dataset note
   "the solution should work even if the number of dimensions increases").
5. **Retraction path.** `session_runs` records every run ever emitted per session. If an event
   arrives with `event_ts` earlier than the session's processed watermark (`ver`), the session is
   reprocessed from scratch: negate its previously emitted runs (append inverse deltas), re-fold,
   re-emit. Cost is bounded to one session, and SummingMergeTree collapses the noise at merge.
6. **Query layer.** Parameterized views (below). Rows in minutes newer than the global watermark
   (= min cursor lag) are labelled `provisional`; the "right now" tile can additionally read
   `session_state` directly for a zero-lag count of open, fresh, gated-active sessions.
7. **Visualization.** One SQL query per dashboard tile against the views; LibreChat + ClickHouse
   MCP over the same views; ClickStack dashboards on `system.query_log` + the ingest-lag metric.

## 1.3 Exact DDL

```sql
-- ============ 0. Ingest cursor (state machine bookkeeping) ============
CREATE TABLE pipeline_cursor
(
    name        LowCardinality(String),
    cursor_ts   DateTime64(3, 'UTC'),
    ver         UInt64
)
ENGINE = ReplacingMergeTree(ver)
ORDER BY name;
-- Engine: single-row-per-key checkpoint, last-write-wins. Replacing is the
-- only engine whose semantics ARE "keep the latest"; volume is trivial.

-- ============ 1. Raw events (immutable log) ============
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
    audio_language_raw String,
    audio_language    LowCardinality(String)
        MATERIALIZED multiIf(lowerUTF8(audio_language_raw) IN ('', 'unk', 'off'), 'unknown',
                             lowerUTF8(splitByChar('-', audio_language_raw)[1])),
    subtitle_language LowCardinality(String),
    player_version    LowCardinality(String),
    session_start     DateTime64(3, 'UTC'),
    ingest_ts         DateTime64(3, 'UTC') DEFAULT now64(3),
    dedup_sig         UInt64 MATERIALIZED
        cityHash64(video_session_id, event_type, event, toUnixTimestamp64Milli(event_ts))
)
ENGINE = MergeTree
PARTITION BY toDate(session_start)
ORDER BY (video_session_id, event_ts)
TTL toDateTime(event_ts) + INTERVAL 90 DAY TO VOLUME 'cold'
SETTINGS index_granularity = 8192;
-- Engine: plain MergeTree, append-only. We deliberately do NOT use
-- ReplacingMergeTree for dedup: at billions of rows FINAL is unaffordable, and
-- the EDA's key warning stands — replacement happens at merge time, AFTER any
-- MV has already fired on both copies. Dedup is logical (dedup_sig, in the
-- state machine) + transport-level (insert_deduplication_token).
-- ORDER BY (session, ts): the state machine reads whole sessions; the sparse
-- index turns that into one contiguous seek. PARTITION BY toDate(session_start)
-- — valid because session_start_epoch is present and correct on 100% of rows —
-- colocates a session's entire lifetime in ONE partition, so day-crossing
-- sessions (11 in the sample) never need cross-partition stitching.

-- ============ 2. Content dimension ============
CREATE TABLE content_dim
(
    content_id  UInt64,
    title       String,
    video_type  LowCardinality(String),
    category    LowCardinality(String),
    updated_at  DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY content_id;
-- Engine: metadata is upserted (titles get remapped, types corrected);
-- Replacing gives idempotent reloads. Read path is the dictionary, which
-- takes the latest row, so FINAL cost is never on the query path.

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

-- ============ 3. Session state (the Flink replacement) ============
CREATE TABLE session_state
(
    video_session_id  String,
    user_id           String,
    -- dimensions PINNED at first event (EDA: 95 platform / 120 user drifts
    -- mid-session would otherwise split one session across two slices)
    content_id        UInt64,
    platform          LowCardinality(String),
    country           LowCardinality(String),
    video_type        LowCardinality(String),
    category          LowCardinality(String),
    -- gate state
    fg                UInt8  DEFAULT 1,   -- foreground default ON (decides 1,125h)
    playing           UInt8  DEFAULT 0,   -- playing default OFF (pre-PLAY = startup)
    last_signal_ts    DateTime64(3, 'UTC'),
    session_ended     UInt8  DEFAULT 0,
    -- run bookkeeping
    open_run_start    Nullable(DateTime('UTC')),   -- minute the current run's +1 was emitted at
    emitted_until     Nullable(DateTime('UTC')),
    ver               UInt64            -- max(event_ts_ms) folded so far = idempotency watermark
)
ENGINE = ReplacingMergeTree(ver)
ORDER BY video_session_id
TTL toDateTime(last_signal_ts) + INTERVAL 1 DAY;   -- eviction ≠ activity timeout
-- Engine: one logical row per session, monotonically versioned upserts,
-- point-reads by session id. Replacing(ver) is exactly this contract; the
-- micro-batch reads with argMax-per-session (never FINAL over the full table).
-- Bounded: ~open sessions only (~1M rows at 100x), TTL evicts the tail.
-- Two timers preserved from DESIGN.md: 90s activity (correctness, in the fold)
-- vs 1-day eviction (memory) — never trade one for the other.

-- ============ 4. Cold path: the single source-of-truth delta table ============
CREATE TABLE cc_delta_content
(
    minute          DateTime('UTC'),
    content_id      UInt64,
    platform        LowCardinality(String),
    country         LowCardinality(String),
    video_type      LowCardinality(String),
    category        LowCardinality(String),
    delta_sessions  Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (content_id, platform, country, video_type, category, minute);
-- Engine: SummingMergeTree because deltas are pure additive integers — the ONE
-- case where Summing is strictly right. Duplicated logical rows (eager +1s,
-- retro-dated −1s, retraction negations, zero-net pairs from hour cuts)
-- collapse at merge for free, and — critically — correctness never depends on
-- merge state, because the query always does sum(delta) GROUP BY minute. No
-- FINAL, ever. Serves the COLD path: hour-self-contained sparse deltas,
-- ~5M rows/day at 100x, ~5 rows per key prefix → a filtered query seeks its
-- prefix and scans a short contiguous run.
-- ORDER BY content-FIRST (reversal of DESIGN.md): this is the drill-down
-- table; its queries always carry content_id. DESIGN.md's platform-first key
-- would scan all platforms' data for a content filter. Dashboard defaults
-- never touch this table (they hit the narrow one), so platform-first buys
-- nothing here.

-- ============ 5. Narrow serving table (dashboard default) ============
CREATE TABLE cc_delta_dims
(
    minute          DateTime('UTC'),
    platform        LowCardinality(String),
    country         LowCardinality(String),
    video_type      LowCardinality(String),
    category        LowCardinality(String),
    delta_sessions  Int64
)
ENGINE = SummingMergeTree(delta_sessions)
PARTITION BY toDate(minute)
ORDER BY (platform, country, video_type, category, minute);
-- Engine: same additive argument. Serves the HOT dashboard default: tiny
-- (~30–3,000 combos even at 100x), platform-first because that is the
-- dominant dashboard filter and the load skew (ANDROID_PHONE = 67% of
-- session-minutes) means the biggest slice benefits most from prefix locality.

CREATE MATERIALIZED VIEW cc_delta_dims_mv TO cc_delta_dims AS
SELECT minute, platform, country, video_type, category, delta_sessions
FROM cc_delta_content;

-- ============ 6. User-grain concurrency (sketches, narrow dims only) ============
CREATE TABLE cc_users_minute
(
    minute      DateTime('UTC'),
    platform    LowCardinality(String),
    country     LowCardinality(String),
    video_type  LowCardinality(String),
    users_state AggregateFunction(uniqCombined(14), String)
)
ENGINE = AggregatingMergeTree
PARTITION BY toDate(minute)
ORDER BY (platform, country, video_type, minute);
-- Engine: AggregatingMergeTree is FORCED here, not chosen — countDistinct(user)
-- is not delta-summable (a user on phone+TV must count once; the EDA found up
-- to 82 sessions-over-users in one minute and one 301-session user). Mergeable
-- uniqCombined sketches are the only representation that merges correctly
-- across parts and across minutes. Populated by the state machine from run
-- segments (arrayJoin over run minutes — bounded because runs are hour-cut,
-- ≤60 minutes each), NARROW dims only: per-minute explosion at content grain
-- is the representation the whole design exists to avoid.
-- Peak user concurrency = max over minutes of uniqCombinedMerge(users_state).

-- ============ 7. Run ledger (retraction + backfill + audit) ============
CREATE TABLE session_runs
(
    video_session_id String,
    run_start        DateTime('UTC'),
    run_end          DateTime('UTC'),
    content_id       UInt64,
    platform         LowCardinality(String),
    country          LowCardinality(String),
    video_type       LowCardinality(String),
    category         LowCardinality(String),
    sign             Int8 DEFAULT 1          -- −1 rows are retractions
)
ENGINE = MergeTree
PARTITION BY toDate(run_start)
ORDER BY (video_session_id, run_start);
-- Engine: append-only ledger; plain MergeTree. Three jobs: (a) exact
-- retraction when an out-of-watermark event forces per-session reprocessing —
-- negate these runs, never guess; (b) backfilling any FUTURE dimension table
-- without replaying raw events; (c) the audit trail that proves to judges the
-- unseen-day answers came from the pipeline. This is the "interval array"
-- representation from the problem statement, kept as a byproduct.
```

## 1.4 Where `DESIGN.md` breaks at scale, and the replacement

| # | `DESIGN.md` element | Why it breaks | Replacement |
|---|---|---|---|
| 1 | Flink stream processor | Violates "all computation in ClickHouse"; a second stateful system to checkpoint, deploy, and explain; no hackathon evidence trail | Micro-batch fold in ClickHouse SQL + `session_state` (§1.2 step 3) |
| 2 | Hourly absolute anchors **and** hour-cut runs | Redundant pair; anchors need invalidation/recompute on late data — a mutation-shaped workload CH punishes | Hour-cut runs only; every hour self-contained, cumsum from hour start |
| 3 | `WITH FILL` densification | Materializes empty minutes per combo; breaks under multi-group fills; day-grain over weeks = 40K+ synthetic rows per combo per query | Sparse step-function math: peak at delta points, duration-weighted average (§1.5) |
| 4 | Two fixed-width tables as the extensibility story | Dataset note says dimensions will grow; a hand-built second table per dimension set doesn't scale organizationally | One source-of-truth wide table + MV cascade + `session_runs` backfill |
| 5 | Wide table `ORDER BY (platform, …, content_id, minute)` | Content drill-down (the table's only job) filters content_id → full-prefix scan across all platforms | `ORDER BY (content_id, platform, …)` — content first |
| 6 | `cc_live` side-table refreshed every 1–2s | Second representation of truth; seam logic at the watermark; a whole write path used only at the live edge | Eager `+1` + 15–30s staleness sweep writing into the *same* delta table; live edge = recent deltas, one label |
| 7 | Dedup "in the stream processor" | With no Flink, undefined; ReplacingMergeTree explicitly (and correctly) ruled out | `dedup_sig` dedupe inside the fold + `insert_deduplication_token` + `ver` idempotency: replaying any batch emits nothing new |
| 8 | Late events: "just append deltas" | True only for events *newer* than the session's processed watermark. An event that lands *inside* already-emitted runs silently corrupts them | `ver` check → per-session retract-and-replay via `session_runs` (§1.2 step 5) |
| 9 | User-level concurrency "needs a sketch table" (hand-waved) | Not designed; naive per-minute user rows at content grain = the forbidden explosion | `cc_users_minute`, narrow dims, hour-bounded arrayJoin (§1.3 table 6) |
| 10 | Single global watermark implied | One stalled ingest source stalls finalization for everything | Watermark = min cursor across sources, exported to ClickStack; provisional labelling is per-minute, not per-table |

## 1.5 Query layer: merging hot + cold under concurrent read load

There is deliberately **no seam**: hot and cold are the same table; "hot" is just rows written in
the last few seconds plus the possibility of a pending retro-dated `−1` (bounded by one sweep
interval, ≤30s, at the live edge — a quantified, disclosed error bar, not a correctness bug).

Canonical peak query (any filter combo, any grain):

```sql
WITH
  toStartOfHour({from:DateTime}) AS ctx_start,   -- hour-self-containment: never read earlier
  sparse AS (
      SELECT minute, sum(delta_sessions) AS d
      FROM cc_delta_content
      WHERE content_id = {cid:UInt64} AND platform = {p:String}
        AND minute >= ctx_start AND minute < {to:DateTime}
      GROUP BY minute
  ),
  stepped AS (
      SELECT minute,
             sum(d) OVER (ORDER BY minute ROWS UNBOUNDED PRECEDING) AS cc,
             leadInFrame(minute, 1, {to:DateTime})
                 OVER (ORDER BY minute ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) AS next_minute
      FROM sparse
  )
SELECT
    max(cc)                                                      AS peak_concurrency,
    sum(cc * dateDiff('minute', greatest(minute, {from:DateTime}), next_minute))
      / nullIf(sum(dateDiff('minute', greatest(minute, {from:DateTime}), next_minute)), 0)
                                                                 AS avg_time_weighted,
    max(minute) > {watermark:DateTime}                           AS provisional
FROM stepped
WHERE next_minute > {from:DateTime};
```

Properties under concurrent load: the WHERE clause is a primary-key prefix seek (~5 rows/prefix
measured); each query reads its own short contiguous range, so reads don't contend; `GROUP BY
minute` makes the answer independent of background merge progress (never `FINAL`); hour/day grain
is `max()`/re-weighting over the same minute series — **peak is never rolled up from a coarser
pre-aggregate, and never summed across slices** (the EDA's 2,786-vs-2,697 trap). Both queries ship
as parameterized views so LibreChat/MCP and the dashboard cannot express the wrong semantics.
Average is served in both declared forms (time-weighted and occupied-minutes); the benchmark
submission states which one is used.

Concurrency-of-queries hygiene at 100x: dashboard defaults pinned to `cc_delta_dims` (thousands of
rows/day — effectively free at any QPS); drill-downs hit `cc_delta_content` behind a per-query
`max_threads` cap; the "right now" tile short-circuits to `session_state` for zero-lag truth.

---

# 2. Edge Cases at 100x

Grouped by blast radius. "Breaks" = what goes wrong if unhandled at scale.

**Correctness-fatal (wrong numbers, forever)**

1. **Duplicate events (0.465% measured).** Breaks: in a delta model an unmatched `+1` corrupts
   every subsequent minute permanently; at 100x that's ~420K poison rows/day. Fix: three layers —
   `insert_deduplication_token`, `dedup_sig` dedupe inside the fold, `ver`-idempotent emission
   (replaying a batch emits nothing).
2. **Pause/resume hidden inside `VideoHeartbeat.event`.** Breaks: filtering on `event_type` alone
   counts all paused time (246h in sample) as active. Fix: gate on `(event_type, event)` jointly;
   the 9-signal reduction is the only place raw names are interpreted.
3. **Heartbeats during background (3,674 measured, incl. 1,657 pauses).** Breaks: "heartbeat ⇒
   watching" re-imports background time. Fix: foreground gate strictly outranks liveness; a signal
   proves *running*, never *visible*.
4. **Foreground default before first marker (decides 1,125h — a third of all time).** Breaks:
   defaulting to background (which 99.7% of sessions superficially suggest) erases the first ~3
   minutes of every session. Fix: default ON — 97% of first `BG` markers arrive after first `PLAY`.
   **Confirm with mentors against ground truth (Q1).**
5. **Per-interval deltas double-count (Bug 1).** Breaks: 7.6% peak overcount. Fix: distinct
   (session, minute) → merged runs → deltas per run. Preserved from `DESIGN.md`.
6. **Sparse cumsum skips minutes (Bug 2).** Breaks: concurrency attributed to wrong minutes. Fix:
   step-function query semantics (§1.5) — same correctness as `WITH FILL`, none of the cost.
7. **Peak summed across slices / rolled up from hourly.** Breaks: every slice peaks at its own
   minute (vod 11:02 vs live 10:42). Fix: minute grain is the only stored grain; `max()` only;
   enforced in the parameterized views.
8. **`countDistinct(user)` treated as delta-summable.** Breaks: multi-device users double-counted;
   the 301-session user alone adds up to 110 phantom "viewers". Fix: `uniqCombined` sketch table;
   session-grain and user-grain are different tables with different engines.
9. **Late event inside already-emitted history.** Breaks: appended deltas double-count minutes the
   session already occupied. Fix: `ver` watermark check → retract-and-replay that one session from
   `session_runs`.
10. **INNER join to content metadata.** Breaks: 250 sessions (2.3%) vanish from every `video_type`
    answer; at 100x, whole titles disappear silently. Fix: `dictGetOrDefault(..., 'unknown')`.
11. **Same-millisecond signal collisions (161,660; 6,058 state-conflicting).** Breaks:
    non-reproducible outputs — different answers per run, fatal for the benchmark. Fix: fixed
    tie-break priority in the sort key of the fold.
12. **Out-of-order events within a session (11.3% of steps, max inversion 43h).** Breaks: state
    carry-forward on arrival order produces garbage gates. Fix: always `arraySort` by
    `(event_ts, priority)`; never trust insertion order; the CSV's session-grouped layout means
    file order is *actively* misleading.

**Fatal on the unseen day specifically**

13. **Open sessions (0 in sample, promised on the unseen day).** Breaks: any code path never
    exercised is broken; P1 (drop open) reads 4 instead of 430 at the live edge, P3 (assume
    active) counts crashed apps forever. Fix: P2 — active until last signal + 90s; eager `+1` +
    staleness sweep implements exactly this; tested by watermark-truncation replay of the sample.
14. **Sessions with no BG/FG markers at all.** Breaks: 100%-background rate is a generator
    fingerprint; code assuming ≥1 BG event NPEs or misgates on real data. Fix: defaults carry the
    session; gates need zero markers to function.
15. **Dimension cardinality shifts (country=1, subtitle≈2 in sample).** Breaks: ordering keys and
    `LowCardinality` choices tuned to degenerate cardinality; a 50-country unseen day changes
    prefix selectivity. Fix: country kept in both keys *after* platform (cheap if degenerate,
    useful if not); nothing assumes single-valued dims.
16. **Heartbeat cadence change (docs say 60s, data says 40s).** Breaks: a 90s timeout against a
    genuinely-60s unseen day reads every second beat as a death. Fix: timeout as a config constant;
    re-measure cadence in the first minutes of unseen-day ingest (one query); EDA shows peak moves
    <0.5% across 45–300s, so adjustment is safe.
17. **Marathon/abandoned sessions (43.6h max; retention 3.7%).** Breaks: without liveness, one
    abandoned TV contributes 43h of phantom concurrency; at 100x, ~1,200 such sessions/day. Fix:
    the 90s liveness gate + hour-cut runs (bounds any single run to 60 min) + 1-day state eviction.
18. **`VideoError` semantics — the two EDAs disagree** (Keshav: 55/293 sessions continue after
    ERR; Rohit: always fatal). Breaks: whichever team-mate's assumption is wrong loses those
    sessions' tails. Fix: do NOT treat ERR as terminal (carry state; if signals continue, gates
    reopen) — the conservative choice that is right in both worlds. Escalated to mentors (Q2).

**Degradation (wrong-ish or slow, not silently wrong)**

19. **Events after `VideoSessionEnd` (802 events, up to 34.7 min late; 213 `END → BG`).** Breaks:
    post-END runs resurrect dead sessions. Fix: `session_ended` flag clamps run emission at END;
    trailing signals update liveness bookkeeping only. (13 `END → PLAY` restarts: covered by
    retract-and-replay if a genuine restart, ignored otherwise.)
20. **Unbalanced BG/FG (418 unmatched BG) & last-event-is-BG tails (344 sessions, 6.7h).** Breaks:
    missing FG read as foreground includes the tail. Fix: carry-forward + liveness cap ends the
    tail at last signal + 90s regardless.
21. **Dimension drift mid-session (95 platform, 120 user changes).** Breaks: one session emits +1
    in slice A and −1 in slice B — both slices permanently wrong. Fix: dims pinned at first event
    in `session_state`; deltas always use pinned dims.
22. **Zero-active sessions (16) and sub-minute intervals (50.1% < 60s).** Breaks: zero-active must
    contribute nothing (half-open `[a,z)` guarantees it); sub-minute occupancy is why the average
    definition swings 4x — must be declared, not discovered. Fix: half-open intervals; both average
    forms served, one declared.
23. **Bot/aggregator users (301 sessions, peak 110 concurrent).** Breaks: session-grain
    concurrency inflated by non-human traffic; at 100x this class is thousands of accounts. Fix:
    don't silently cap (business call) — expose `sessions_per_user` guardrail metric + optional
    filtered view; ask mentors (Q6).
24. **Day-crossing sessions (11) + hour-crossing (3,882).** Breaks: day-partitioned delta reads
    miss the carry-in at window start. Fix: hour cuts make carry-in explicit (+1 re-emitted at
    boundary); partition-by-session_start on raw keeps the fold single-partition.
25. **Tiny-insert flood at 100x (many parts).** Breaks: "too many parts" rejections; merge debt;
    MV cascade amplifies every insert ×3. Fix: batch ≥10K rows (buffer at gateway or async_insert),
    micro-batch cadence ≥15s, monitor `system.parts` via ClickStack.
26. **`BufferStart`/`BufferEnd` misread as inactivity.** Breaks: fragments nearly every session
    (66K buffer events, p50 0.4s) and undercounts. Fix: buffering is watching; buffer events are
    liveness evidence only. Same class: `download_*` events are NOT activity (26% fire while
    backgrounded) and must never open a run.
27. **Query-time densification under dashboard fan-out.** Breaks: 50 concurrent dashboard users ×
    `WITH FILL` over 30 days = millions of synthetic rows per second of pure waste. Fix: step-
    function queries (§1.5); fill only at render time.

---

# 3. Clarifying Questions for Mentors

Ordered by expected impact on benchmark correctness × cost of guessing wrong.

**P0 — these decide whether a correct system scores zero**

1. **What exact active-session definition generates the ground truth?** Specifically: (a) does a
   session count in minute M if active at *any instant* within [M, M+1), or only at the minute
   boundary? (b) foreground default before the first BG/FG marker — in or out? (c) is time before
   the first `VideoPlay` excluded? (d) what liveness timeout, if any, and measured from which
   signals? Our two internal EDAs produced peaks of **2,697 vs 2,316 on identical data** purely
   from definitional differences — this single question is worth more than any engineering choice.
2. **Is `VideoError` terminal?** Our analyses contradict each other (0% vs 19% of errored sessions
   continue). Which does the answer key assume?
3. **Which "average concurrency" does the answer key use** — mean over occupied minutes (34.85 on
   sample), mean over all minutes in range (7.47), or time-weighted (28.99)? A 4x spread; a correct
   system with the wrong definition fails every average question.
4. **Session-level or user-level concurrency for the benchmark?** They differ by up to 82 at a
   single minute, and the storage engines required are different (Summing deltas vs uniq sketches).
5. **Unseen day: delivery and evaluation mechanics.** One CSV drop or a stream? Must results
   demonstrate *incremental* absorption (replay with the pipeline live), or is bulk-load-then-query
   acceptable? Will it actually contain open sessions and events later than `VideoSessionEnd` of
   day boundary? What counts as sufficient "pipeline evidence" — `system.query_log`, our
   `session_runs` ledger, ClickStack traces?

**P1 — these change the schema if answered surprisingly**

6. **Should non-human traffic (e.g., the 301-session user) be included in concurrency?** If
   excluded, by what rule — we need it at ingest, not post-hoc.
7. **Benchmark filter surface:** which dimension combinations appear in the fixed query set? Is
   `content_id` filtered alone, or always alongside platform/country? Do `title`/`category`
   filters appear (i.e., must enrichment be query-time-consistent with metadata updates)? Any
   filters on `app_version` / `audio_language` / `player_version` (if yes, they enter the wide
   table's dims now, not later)?
8. **Unseen-day distribution shifts we should provision for:** multiple countries? new platforms?
   heartbeat cadence still 40s? total volume vs the 905K sample? peak concurrency order of
   magnitude?
9. **Timezone and grain semantics:** are day/hour buckets UTC? Half-open windows `[from, to)`? If
   two minutes tie for peak, is reporting the value alone sufficient?
10. **Late-arrival tolerance:** is there a bound on how late events arrive on the unseen day
    (sample shows ≤35 min after session end)? Is there a cutoff after which the answer key itself
    ignores an event?

**P2 — infrastructure and scoring**

11. **Is a thin external scheduler (cron/Python issuing pure ClickHouse SQL) within the "all
    computation in ClickHouse" rule**, or must orchestration itself be CH-native (refreshable
    materialized views)? Is Kafka in front of ClickHouse acceptable or superfluous for scoring?
12. **ClickHouse Cloud sizing:** what service tier are latencies normalized to, and are settings
    like `max_threads`, projections, and multiple MV cascades fair game?
13. **How is the ClickStack/Langfuse/LibreChat integration weighted**, and does depth in one beat
    breadth across two? (Our natural fit: ClickStack on ingest-lag + query latency, LibreChat+MCP
    over the parameterized views.)
14. **`session_start_epoch` reliability:** guaranteed present and correct on every row on the
    unseen day (we partition raw data by it)?
15. **Scoring of the provisional live edge:** benchmark queries presumably target closed history —
    confirm no benchmark question will be asked *inside* the watermark window, where any honest
    system carries a bounded staleness error.
