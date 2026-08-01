# The Design, In Plain Words

Goal: answer "how many sessions are truly watching at minute M?" fast, for any filter
(platform, country, content, video type), while data keeps arriving.

---

## Idea 1 — Every session is just two switches and a clock

Forget the 48 event names. For each session we track only:

| Thing | Set by | Meaning |
|---|---|---|
| `foreground` | `AppForegrounded` → ON, `AppBackgrounded` → OFF | is the app on screen? |
| `playing` | `VideoPlay` / `resume` → ON, `pause` → OFF | is the video playing? |
| `last_seen` | **any** event | when did we last hear from it? |

**A session is COUNTED right now only if all three pass:**

```
foreground == ON   AND   playing == ON   AND   (now - last_seen) <= 90 seconds
```

Starting values: `foreground = ON` (sessions start visible — proven by the data:
97% of first background events come *after* play starts), `playing = OFF` (nothing
is playing until `VideoPlay` arrives).

### Why switches instead of remembering events?

Because the switch **is** the memory. Example — the tricky `pause → foreground` case:

```
event                 foreground   playing   counted?
--------------------  ----------   -------   --------
playing normally         ON          ON        YES
pause                    ON          OFF       no
AppBackgrounded          OFF         OFF       no
AppForegrounded          ON          OFF       no   ← back on screen, but STILL PAUSED
resume                   ON          ON        YES
```

Each event flips only its own switch. Coming to the foreground doesn't un-pause you.
And garbage sequences (pause-pause, FG-with-no-BG, events while backgrounded) are
harmless: flipping a switch to where it already is does nothing.

Two traps this avoids (both real in the data):
- **pause/resume live inside `VideoHeartbeat`** (the `event` column), not as their own
  event_type. Look at both columns.
- **Heartbeats keep firing from pockets** (3,674 measured while backgrounded).
  A heartbeat only updates `last_seen`. It never opens the foreground switch.

---

## Idea 2 — Store when the count CHANGES, not the count itself

As events flow, each session's counted/not-counted status turns ON and OFF.
Each ON-stretch, rounded to minutes, is a **run**: e.g. "counted from 10:02 to 10:17".

We store each run as two tiny rows:

```
run 10:02 → 10:17   becomes   (10:02, +1)  and  (10:17, -1)
```

Concurrency at any minute = running total of these +1/−1 rows. A 3-hour session is
still just 2 rows. That's the whole scaling story: **heartbeats arrive every 40s, but
84% of them change nothing** (same minute, still counted), so database writes stay tiny
no matter how big traffic gets.

Two known bugs of this model, both fixed:
1. **One session, not one interval.** If a session pauses/resumes twice inside a minute,
   it's still ONE viewer that minute. Merge a session's counted-minutes into runs first,
   *then* emit +1/−1. (Unfixed, peak is 7.6% too high.)
2. **The running total must not skip quiet minutes.** Concurrency is flat between
   changes — the query treats it as a step function (details in LLD).

---

## Idea 3 — Cut every run at the top of the hour

A run crossing 11:00 is stored as two runs: `…→11:00` and `11:00→…`. The numbers don't
change (−1 and +1 at 11:00 cancel), but now **every hour is self-contained**: to know the
count at 11:23 you only ever read deltas from 11:00 onward — sessions already watching
at 11:00 re-announced themselves with a +1 there. No query ever reads more than 60
minutes of history it doesn't want.

---

## Idea 4 — Live sessions: announce early, retract if wrong

Open sessions (still watching right now) need no special table:

- When a run **opens**, write its `+1` immediately. Don't wait for it to end.
- A sweep every ~30s writes the `−1` for sessions that went silent (90s rule),
  backdated to when they actually went silent. Adding rows late is fine — it's
  addition; nothing needs rewriting.

So "live" and "history" are the same table. The freshest few minutes are labelled
*provisional*; everything older is *final*.

Wifi drop for 4 minutes? Count stops at silence+90s (correct — they weren't watching),
and when they return a new run just opens. Nothing to repair.

---

## Idea 5 — The whole pipeline, ingestion → visualization, with WHEN everything happens

ClickHouse work happens at four distinct moments. Every object in this design belongs to
exactly one of them:

| Moment | Frequency | What runs |
|---|---|---|
| **Setup** | once | `CREATE TABLE`s, materialized views, the content dictionary |
| **Insert** | every incoming batch | column defaults, normalization, the SI materialized view |
| **Batch** | every 15–30s | the state machine job (the only "brain") |
| **Query** | per dashboard request | prefix seek + running sum / sketch merge |

```
                          ┌────────────── SETUP (once) ──────────────┐
                          │ tables · MVs · content_dict (dictionary) │
                          └──────────────────────────────────────────┘

 STAGE 1 · INGEST (continuous)
 Kafka / CSV replayer inserts batches (≥10K rows) into events_raw.
 AT THE MOMENT OF INSERT, synchronously, on just that block of rows:
   • defaults fire: ingest_ts = now()          (our arrival stamp)
   • normalization fires: audio_language cleaned
   • the SI materialized view fires: it sees ONLY the inserted block,
     enriches it via dictGet (in-RAM lookup), aggregates it per minute,
     and appends to cc_si_minute.  ← session-independent view is DONE here.
 Nothing reads any table at this stage. No query is run. Insert returns.

 STAGE 2 · BATCH (every 15–30s) — the session-aware brain
   reads:  pipeline_cursor            (where did I stop last time?)
           events_raw                 (ONLY rows with ingest_ts > cursor)
           session_state              (switches for just the sessions seen)
           content_dict               (in-RAM enrichment, pins dims at session start)
   computes: flip switches → detect run open/close → hour cuts
   writes: cc_delta_content (+1/−1)   ← and AT THAT INSERT, its MVs fire:
              └─► cc_delta_dims        (narrow copy, auto)
              └─► cc_users_minute      (user sketches, auto)
           session_runs               (ledger)
           session_state              (new switch values, versioned)
           pipeline_cursor            (advance)
   plus the staleness sweep: reads session_state for silent-90s+ sessions,
   writes their backdated −1 into cc_delta_content.

 STAGE 3 · QUERY (per request) — dashboards NEVER touch events_raw
   default view   → cc_delta_dims      sum deltas, running total, max/avg
   drill-down     → cc_delta_content   same, seeks ~5 rows via sort key
   "right now"    → session_state      count open switches directly
   people-count   → cc_users_minute    merge sketches, max per minute
   validation     → SA curve vs SI curve (SA must be ≤ SI)

 STAGE 4 · VISUALIZATION
   dashboard tiles = the parameterized views above · LibreChat+MCP asks the
   same views in English · ClickStack watches ingest lag (now − max ingest_ts),
   cursor lag, query latency from system.query_log.
```

Three timing facts that make this hang together:

- **A materialized view is not a stored query — it's an insert trigger.** It runs at the
  instant rows are inserted into its source table, sees *only those new rows*, transforms
  them, and appends to its target. It never re-reads history. That's why SI costs nothing
  and why deltas fan out to the narrow table for free.
- **A dictionary is a pre-loaded in-RAM hash map.** Created once at setup, it re-reads
  `content_dim` every 60–300s in the background. `dictGet` during insert or batch is a
  memory lookup, not a join — this is *when* enrichment happens: at MV time for SI, at
  batch time (pinned once per session) for SA. Queries never join metadata.
- **The batch job never rescans.** Each run reads only the new slice of `events_raw`
  (cursor) and only the touched sessions' switches. History is written once and left alone.

Why the table split: adding `content_id` makes the delta table 9× bigger. Dashboard
defaults (platform/country/type) shouldn't pay for that; drill-downs use the big table
and land on ~5 rows via the sort key. User sketches are separate because distinct users
can't be +1/−1'd (a phone+TV user must count once) — different math, different engine.

---

## Idea 6 — Two views of concurrency: session-aware and session-independent

The brief asks for both, and they answer differently on purpose.

| | **Session-aware (SA)** — the main path | **Session-independent (SI)** — the cross-check |
|---|---|---|
| Question it answers | "who was truly watching?" | "who was *present* (emitted a signal)?" |
| How | switches + runs + deltas (Ideas 1–5) | count distinct sessions/users that emitted any non-inactive event in each minute — **no state, no memory** |
| Machinery | state machine, cursor, batches | one materialized view straight off `events_raw`; fills itself on insert |
| Correctness | excludes paused/backgrounded/stale time (the 3 gates) | overcounts: pocket heartbeats and paused-but-beating sessions leak in (~+9% at peak: 2,944 vs 2,697 on the sample) |
| Update handling | needs the retraction path for late events | trivial — a late event just lands in its minute, done |
| Storage | 2 rows per run (tiny) | 1 row per minute × dims (the "explosion" — but with sketches, acceptable at narrow dims) |
| Fails when | the batch pipeline stalls | never (it's insert-time), which is exactly why we keep it |

How we use SI:
1. **Validation** — SA must always be ≤ SI per minute per slice. If SA ever exceeds SI, the
   state machine has a bug. Cheap, continuous, automatic.
2. **Fallback & freshness band** — SI is alive even if the batch job dies; dashboards can
   show "between X (SA, true) and Y (SI, present)" at the live edge.
3. **The demo comparison the judges asked for** — one chart, two lines; the gap between
   them *is* the phantom audience this problem exists to remove.

## Three rules the queries must obey

1. **Never add peaks.** Android peaks at 10:56, iPhone at 10:55. Sum of peaks (2,786)
   ≠ real peak (2,697). Always: build the minute curve for the *exact filter asked*,
   then take max.
2. **Peak only from minute grain.** Max of hourly averages is a different, wrong number.
3. **Say which "average".** Over occupied minutes (34.85), over all minutes (7.47), or
   time-weighted (28.99) — a 4× spread on the same data. We compute the first and third;
   the benchmark answer declares which one it is.

---

## What can still go wrong, in one breath

Duplicated events would poison the +1/−1 stream forever → dedupe before emitting.
Events arriving out of order (11.3% do) → always sort by event time first.
An event arriving *for the past*, inside runs we already emitted → re-do just that one
session: write the exact negative of its old runs, recompute, re-emit (we keep a ledger
of every run emitted, per session, for exactly this).
Everything else — open sessions, 43-hour zombies, missing markers, post-END events —
falls out of the switches + 90s rule automatically.

→ Full table definitions, the batch algorithm, and the exact SQL live in `LLD.md`.
