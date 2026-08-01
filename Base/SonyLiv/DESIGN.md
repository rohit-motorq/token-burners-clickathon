# Foreground-Only Concurrency — The Algorithm, in Plain Words

How to count "how many people are watching right now" without counting people whose app
is sitting in their pocket.

Every number quoted here was measured on the provided dataset
(905,558 events / 10,866 sessions). Full detail in `DATA_ANALYSIS_REPORT.md`.

---

## Part 1 — The Problem in One Picture

A session is not a single block of watching. It looks like this:

```
session starts                                                    session ends
   │                                                                    │
   ▼                                                                    ▼
   ├──── watching ────┬─ paused ─┬─── watching ───┬─ backgrounded ─┬── watching ──┤
   │                  │          │                │  (in pocket)   │              │
   0min              5min       7min            12min            18min          22min

   Naive answer:  22 minutes of viewing.
   Real answer:   16 minutes of viewing.
```

The naive answer counts the pocket time and the paused time. On the real dataset that
mistake makes **peak concurrency look 38.6% bigger than it actually is** (3,739 instead
of 2,697).

So the whole job is: find the *green* stretches, and count only those.

---

## Part 2 — Step 1: Turn Messy Events Into Clean Signals

The raw data has 48 different event names. Most of them are noise for our purpose. We
squash them into **9 signals**:

| Signal | Comes from | Means |
|---|---|---|
| `START` | `VideoSessionStart` | session opened |
| `PLAY` | `VideoPlay` | playback began |
| `PAUSE` | heartbeat `event = pause` | playback paused |
| `RESUME` | heartbeat `event = resume` | playback resumed |
| `BG` | `AppBackgrounded` | app went off-screen |
| `FG` | `AppForegrounded` | app came back on-screen |
| `HB` | any other heartbeat | "I'm still alive" |
| `ERR` | `VideoError` | something broke |
| `END` | `VideoSessionEnd` | session closed |

### ⚠️ The trap that catches most people

**Pause and resume are hidden inside `VideoHeartbeat`.** They are not their own event
type. If you filter on `event_type` and ignore the `event` column, you will silently
miss every pause in the dataset.

### ⚠️ The second trap

**A heartbeat does NOT mean the user is watching.** We measured 3,674 signals across
2,361 sessions that arrive *while the app is backgrounded* — including 1,657 pause
markers. Audio keeps playing in your pocket and the app keeps talking to the server.

> **Rule: a heartbeat proves the app is *running*. It does not prove it is *visible*.**

---

## Part 3 — Step 2: The Three Gates

At any instant, a session is *actually watching* only if **all three** gates are open.

```
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
   │  Gate 1     │    │  Gate 2     │    │  Gate 3     │
   │ FOREGROUND  │ ─► │  PLAYING    │ ─► │   FRESH     │ ─► ACTIVE ✓
   │ on screen?  │    │ not paused? │    │ still alive?│
   └─────────────┘    └─────────────┘    └─────────────┘
     any one closed  ──────────────────────────────────►  NOT ACTIVE ✗
```

### Gate 1 — Foreground

Look back for the most recent `BG` or `FG`. If it was `FG`, the gate is open.

**Default before any marker is seen: OPEN (assume foreground).**

<details>
<summary>Why this default matters more than anything else in the design</summary>

This default decides **1,125 hours** — more than a third of all the time in the dataset.

The naive reading says default to *background*: 99.7% of sessions have `AppBackgrounded`
as their very first state marker. Looks obvious.

**It's wrong.** 97% of those `BG` markers arrive *after* the first `PLAY`, and the most
common session opening is `START → PLAY → HB → HB` (72.9% of sessions). So the sequence
is:

```
START → PLAY → (watching, no marker yet) → BG ← the user is LEAVING here
```

The `BG` marker is the user **walking away** from a session that was clearly on screen.
It is not evidence the session began hidden. Default to background and you erase the
first ~3 minutes of every single session.
</details>

### Gate 2 — Playing

Look back for the most recent `PLAY`, `RESUME`, or `PAUSE`. Open if it was `PLAY` or
`RESUME`.

**Default before any marker: CLOSED.** Before `PLAY` arrives the player is still
buffering — nothing is on screen yet. (Only 31 hours at stake, but it's the honest call.)

### Gate 3 — Fresh

Open only if we are within **90 seconds** of the last signal from this session.

<details>
<summary>Why 90 seconds and not the 60 the docs say</summary>

The data dictionary says heartbeats arrive every 60s. **They don't.** The real cadence is
**40 seconds**, measured precisely: `buffer-health` and `video-resize` have 25th, 50th,
and 75th percentile gaps of *exactly* 40s.

90s ≈ 2 missed beats. That's the reasoning — not curve-fitting. (We tested 45s through
300s; peak moved less than 0.5%, so the exact value is safe, but 90s is the principled
one.)

This gate exists for one reason: **one session in the dataset lasts 43.6 hours.** Without
a freshness cap, one abandoned phone contributes 43 hours of fake viewers.
</details>

**Important:** freshness looks at *any* signal, not one chosen heartbeat type. We checked
whether a single heartbeat family could do the job:

| Using only this heartbeat | Sessions it never sees |
|---|---|
| `network-activity` | 1,236 missed |
| `buffer-health` | 1,284 missed |
| all three main ones combined | 1,158 missed |
| **any signal at all** | **0 missed** ✓ |

---

## Part 4 — Step 3: Build the Active Intervals

Walk each session's signals in time order. Between each signal and the next, ask the three
gates. Keep the stretches where all three are open.

```
signals:  START   PLAY        PAUSE      RESUME       BG          FG      END
time:      0:00   0:02        5:00        7:00      12:00      18:00    22:00
           │      │            │           │          │          │        │
gate FG:   open ──────────────────────────────────► CLOSED ──► open ──►
gate PLAY: closed─┬─ open ──► CLOSED ──┬─ open ─────────────────────────►
                  │                    │
result:           ├──── ACTIVE ────┤   ├─ ACTIVE ─┤             ├ ACTIVE ┤
                     0:02 → 5:00         7:00→12:00              18:00→22:00
```

One session → **three** active intervals. On average sessions produce **2.96** intervals;
one produces 169.

**Two housekeeping rules:**
- Use half-open intervals `[start, end)`. Zero-length stretches vanish automatically.
- Break ties for signals sharing the same millisecond with a fixed priority
  (`START`, `PLAY`, `FG`, `RESUME`, `HB`, `ERR`, `PAUSE`, `BG`, `END`). There are 161,660
  same-millisecond collisions — without a fixed order, the same input gives different
  answers on different runs.

---

## Part 5 — Step 4: Intervals → Minutes → Deltas

This is where the classic "+1 at start, −1 at end" trick goes wrong. Two bugs, both real.

### 🐞 Bug 1: Don't emit deltas per interval

Concurrency counts **distinct people**, not intervals. Watch what happens with a session
that pauses briefly inside one minute:

```
minute 10:  ├─active─┤ ├─active─┤       ← one person, two intervals
             +1   -1   +1    -1
             
Naive sum → 2 people in minute 10.   WRONG. It's 1 person.
```

We found **4,854** of these phantom `+1`s. They pushed peak from the true 2,697 up to
2,902.

**This happens a lot.** Any of these inside a single minute causes it:

```
BG → FG           user glances at a notification and comes back
pause → play      user pauses and resumes
pause → play → pause → play    ...twice
```

**12.75%** of all session-minutes (3,476 of 27,268) contain more than one active
interval. Worst case in the real data: **21 intervals in one minute**. Pause/resume churn
is the main cause — pure background churn is rarer, because backgrounding usually lasts
past a minute boundary.

Tested with deliberately injected patterns (`out_10.txt`):

| Pattern in one minute | Intervals | Counted as |
|---|---|---|
| `BG → FG` | 2 | **1** ✓ |
| `pause → play → pause → play` | 3 | **1** ✓ |
| 10 rapid flips | 6 | **1** ✓ |
| `BG` and `FG` in the same millisecond | 1 | **1** ✓ |

Without the dedupe step those 7 test sessions would report **15 viewers instead of 7**.

**The fix — three small steps:**

```
1. EXPLODE   which minutes does each interval touch?
             interval 0:02→5:00  →  minutes {0,1,2,3,4,5}

2. DEDUPE    keep distinct (session, minute) pairs
             two intervals in minute 10  →  one entry

3. MERGE     glue neighbouring minutes into runs
             minutes {0,1,2,3,4,5} →  one run [0 … 5]
```

Then emit deltas **per run**, never per interval:

```
run [0 … 5]   →   +1 at minute 0,   −1 at minute 6
```

This collapsed 32,122 intervals into 16,518 runs, and made the answer exactly correct.

### 🐞 Bug 2: The delta table has holes

Only minutes where something *changed* get a row. In our data: 1,490 minutes have a delta
row, but **3,649 minutes actually have viewers**. A running total over just the existing
rows skips the quiet minutes and puts the numbers in the wrong places.

**The fix:** when querying, generate every minute in the requested window first, then
carry the running total across the gaps.

```
delta rows:      min 0: +5    (nothing)    (nothing)    min 3: -2
                    │                                       │
fill the gaps:   min 0: +5    min 1: 0     min 2: 0     min 3: -2
running total:      5            5            5            3   ✓
```

ClickHouse does this natively with `ORDER BY minute WITH FILL STEP toIntervalMinute(1)`.

### ✅ Proof it works

Both fixes together were checked against brute-force counting (literally counting
overlapping sessions minute by minute):

```
ASSERTION global:    minute-run delta + dense fill == brute force -> PASS
ASSERTION per-slice: 0 mismatching group-minutes                  -> PASS
```

Peak 2,697 and average 34.8476 match exactly — overall *and* for every
platform × content combination.

---

## Part 6 — The Live Path (Hot) and the History Path (Cold)

Two paths, split cleanly by a single moving line called the **watermark**.

```
   past ◄─────────────────────── WATERMARK ──────────────────────► now
        
        COLD PATH                    │           HOT PATH
        finished, trustworthy        │           still changing
        stored as +1/−1 deltas       │           stored as live counts
        answers "last Tuesday"       │           answers "right now"
        label: final                 │           label: provisional
```

They never overlap, so there's no risk of counting anyone twice at the seam.

### Two timers, not one

The most common design mistake is using one timeout for two different jobs:

| Timer | Value | Job | Type of decision |
|---|---|---|---|
| **Activity** | 90s | when we stop *counting* you | correctness |
| **Eviction** | 10min | when we *forget* your session | memory management |

Keeping them separate means you never have to trade an accurate number against server
memory.

### 🔌 "What if someone's internet drops for 4 minutes?"

This is the question everyone asks. The answer is that **nothing special happens** — and
that's by design, not by adding a special case.

```
10:00  watching normally               → counted ✓
10:01  wifi dies
10:02  90s of silence → activity timer  → NOT counted ✓  (correct! not watching!)
       ...but session memory kept alive (eviction is 10 min, not 90s)
10:05  wifi returns, heartbeat arrives  → new run opens, counted again ✓
```

Their history is intact. The 4-minute hole is correctly excluded — **filling it in would
be the bug.**

And if the outage were longer than 10 minutes so the memory *was* released? Still fine. A
new entry with the same session id just produces another run. Runs are independent and
deltas simply add up, so nothing needs repairing.

This isn't a rare path either — **4,511 of 10,850 sessions (42%) have more than one run**.
It's a common case, so it gets exercised constantly instead of quietly rotting.

### 🔑 The change that makes this actually work

Two rules that turn the hot path from "unbounded" into "bounded":

**1. Emit the `+1` immediately when a run opens.** Don't wait for the run to finish.
Otherwise a 43-hour session contributes nothing to history for 43 hours, and the live path
has to serve 43 hours of data. That breaks.

**2. Cut runs at hour boundaries.** A run crossing 11:00 becomes two runs. This is
**free** — the deltas `+1@11:00` and `−1@11:00` cancel out, so the numbers don't change at
all. But it guarantees the live path only ever holds one short segment per session, no
matter how long someone watches.

---

## Part 7 — Storage Layout

```
CREATE TABLE cc_delta (
    minute      DateTime,
    platform    LowCardinality(String),
    country     LowCardinality(String),
    video_type  LowCardinality(String),
    content_id  UInt64,
    delta       Int32
) ENGINE = SummingMergeTree
PARTITION BY toDate(minute)
ORDER BY (platform, country, video_type, content_id, minute);
```

Why this shape:

- **`SummingMergeTree`** — deltas add up, so the engine collapses duplicate rows for free
  during merges. Perfect match.
- **`ORDER BY (dims…, minute)`** — a filtered query jumps straight to its slice and reads
  one short continuous stretch. About 5 rows per key prefix in our data.
- **`PARTITION BY toDate`** — plus the hour-boundary cut from Part 6, deltas never
  straddle two partitions.

### Add hourly anchors

Pure deltas have a hidden flaw: a running total needs *every delta since the beginning of
time*. Asking about last Tuesday would scan years of data.

Fix: store the absolute number once per hour. Then any query reads **one anchor + at most
60 minutes of deltas**, no matter how old the question is.

### Two table widths, not one

`content_id` has 3,357 values and inflates the table **9x** (2,871 rows → 22,676 rows).
But it's also one of the most-requested filters. So run two:

| Table | Dimensions | Use |
|---|---|---|
| narrow | platform, country, video_type | dashboard default — fast |
| wide | + content_id | drill-down on a specific title |

One giant all-dimensions cube does not stay small.

---

## Part 8 — Two Query Rules the Database Can't Enforce

These have to live in the API layer, because SQL will happily give you a wrong answer.

### ❌ Never add peaks together

```
ANDROID_PHONE peak:  1,709  (at 10:56)
IPHONE peak:           329  (at 10:55)   ← different minute!
SONY_TV peak:          280  (at 10:53)   ← different again!
...
Sum of all platform peaks:  2,786
Actual overall peak:        2,697   ← the real answer
```

Every slice peaks at its **own** minute. `vod` peaks at 11:02, `live` at 10:42 — twenty
minutes apart. So peak must always be computed as `max()` over minute-level numbers *at
the exact filter combination asked for*. Never summed from parts, never taken from an
hourly summary.

### ❌ "Average" needs a definition

The same question has three honest answers:

| How you count it | Answer |
|---|---|
| average over minutes that had viewers | **34.85** |
| average over every minute in the range (including empty ones) | **7.47** |
| time-weighted (credit partial minutes properly) | **28.99** |

A **4.7x spread**. It happens because 50.1% of active intervals are shorter than one
minute — so "was this person here for the whole minute?" genuinely matters.

**Pick one, write it in the API docs, and state it next to the number.**

### One more: user-count needs a different table entirely

If someone asks "how many *people*" instead of "how many *streams*", this model doesn't
work. Counting distinct users can't be done with +1/−1 — someone watching on a phone and a
TV must count once, not twice. (We found 17,397 overlapping same-user session pairs, and
up to 82 extra streams in a single minute.)

That needs a sketch-based table (`AggregateFunction(uniqCombined, …)`), kept separately.

---

## Part 9 — Things That Will Bite You

### 🔴 Duplicate events are permanently damaging here

In most systems a duplicate is a minor annoyance. In a delta model, an extra `+1` with no
matching `−1` corrupts **every number from that minute onward, forever**.

The dataset already has 4,210 duplicate rows (0.465%) from at-least-once delivery.

Handle it in the stream processor, where per-session state already exists. Make delta
emission a pure function of state changes so replaying the same event twice produces the
same output.

> **Trap to avoid:** don't try `ReplacingMergeTree` + a materialized view. The view fires
> when rows are *inserted*, not when they're later replaced — so it sees both copies and
> the dedup never reaches your totals.

### 🟢 Late data is genuinely easy (the model's best feature)

A late event is just… more deltas. Append them. No reading old rows, no rewriting history.
This is the single biggest advantage over keeping a mutable row per session.

**One honest caveat:** we *cannot measure* lateness in the provided data. There's no
arrival-time column, and the file is grouped by session rather than sorted by time — which
makes 7.9% of neighbouring rows look "late" purely as an artifact of the file layout. So
add your own arrival timestamp (`DEFAULT now()`) to measure it for real.

### 🟡 Events arrive out of order — 11.3% of them

Within a single session, 11.3% of steps go backwards in time. **Always sort by event time
explicitly.** Never trust arrival order for the gate logic.

### 🟡 Impossible sequences happen anyway

Real transitions found in the data:

```
RESUME → RESUME   1,778 times
END    → BG         213 times   ← session sends signals AFTER it ended!
PAUSE  → PAUSE      189 times
BG     → BG          98 times
```

Carrying state forward handles all of these for free (a second `BG` changes nothing).
Hand-written pattern rules would not — there are **2,655 distinct session shapes** in the
data, and the most common one covers just 10.8%.

### 🟡 Don't trust two things this dataset happens to do

Both look like properties. Neither is safe:

1. **Every session here has an end event.** The problem statement promises open sessions
   on the unseen day. Build and test that path anyway.
2. **Every session here backgrounds at least once.** That's a quirk of how the test data
   was generated. Handle a session with no state markers at all.

### 🔴 No single "correction factor"

Tempting shortcut: measure that we remove ~41% of time, then just multiply naive numbers
by 0.59. **It doesn't work.** How much time survives the gates:

| Session type | Time kept |
|---|---|
| normal (one background trip) | 64.1% |
| several background trips | 57.7% |
| ended while backgrounded | 49.4% |
| marathon (>6 hours) | **3.7%** |

A 17x spread. And by content: `live` loses 48% of its time, `vod` loses 40%. Any single
multiplier is wrong for every group.

---

## Part 10 — The Whole Thing on One Page

```
CLIENT
  │  heartbeat every ~40s
  ▼
KAFKA  ── keyed by session_id  (so each session's events stay together & in order)
  │
  ▼
STREAM PROCESSOR (Flink) — one small state record per session
  │   remembers: dimensions, current run start, last seen time,
  │              foreground on/off, playing on/off
  │
  │   for each event:
  │     1. update the three gates
  │     2. did a run open or close?  →  emit +1 / −1
  │     3. otherwise  →  emit nothing
  │
  │   ⭐ 84% of heartbeats emit NOTHING (same minute as the last one).
  │      Heartbeat volume does not become database write volume.
  │      This is why the whole design holds up at scale.
  │
  ├──────────────────────────┬─────────────────────────────┐
  ▼                          ▼                             │
+1/−1 deltas          live counts every 1–2s               │
  │                    (pre-aggregated, NOT                │
  ▼                     per-session rows)                  │
CLICKHOUSE                   │                             │
 cc_delta  ◄──────────────── cc_live                       │
 (history)                   (right now)                   │
  │                          │                             │
  └────────► v_concurrency ◄─┘   one view, split by watermark
                   │
                   ▼
              DASHBOARD  —  a single SQL query,
                            rows tagged 'final' or 'provisional'
```

### The five things that make it correct

1. **Three gates**, not just heartbeat presence. Heartbeats fire from pockets.
2. **Default to foreground** before the first marker. Decides a third of all time.
3. **Deltas per merged minute-run**, never per interval. Otherwise you double-count.
4. **Fill the minute gaps** when querying. Otherwise the running total drifts.
5. **Never add peaks; always declare your average.** Every slice peaks at its own minute.

### The two things that make it fast

1. **Deltas, not minute rows.** A longer session adds *one row per minute* the naive way,
   but still only *two deltas*. That gap widens as sessions get longer, which is exactly
   why the approach survives 100x growth.
2. **Sort key matches the filters**, so a filtered query reads one short continuous
   stretch instead of scanning.

### The two things that make it update-friendly

1. **Separate activity and eviction timers**, so accuracy never trades against memory.
2. **Emit `+1` eagerly and cut runs at hour boundaries**, so the live path stays small
   whether a session lasts 4 minutes or 43 hours.

---

## Appendix — Parameters in One Table

| Parameter | Value | Where it comes from |
|---|---|---|
| Heartbeat cadence | 40s | measured (p25 = p50 = p75) |
| Activity timeout | 90s | ~2 missed beats |
| Eviction timeout | 10min | memory sizing |
| Interval type | half-open `[a, z)` | kills zero-length stretches |
| Foreground default | ON | decides 1,125 h; sequence proves it |
| Playing default | OFF | pre-`PLAY` is buffering |
| Delta unit | merged minute-run | avoids double-counting |
| Serving grain | 1 minute | peak can't be rebuilt from coarser |
| Anchor interval | 1 hour | bounds the running total |
| Run segmentation | hour boundary | free; bounds hot path |

---

## Where to Verify Any of This

| Claim | File |
|---|---|
| 40s cadence, heartbeat classification, background firing rates | `analysis/out_07.txt` |
| Sequences, defaults justification, gate interaction, archetypes | `analysis/out_08.txt` |
| Gate impact, timeout sweep | `analysis/out_02.txt` |
| **The two delta bugs + correctness proof** | `analysis/out_03.txt` |
| Edge case census with policies | `analysis/out_04.txt` |
| Open sessions, incrementality | `analysis/out_05.txt` |
| Table sizing by dimension set | `analysis/out_06.txt` |
| Edge case audit (16 PASS / 3 GAP / 0 FAIL) | `analysis/out_09.txt` |
| **Intra-minute flapping (BG/FG, pause/play churn)** | `analysis/out_10.txt` |

Reproduce everything: `cd analysis && ./run_all.sh --fresh`

**Note on scope:** the algorithm is validated in DuckDB against brute-force counting. The
ClickHouse SQL in Parts 7–8 is a design sketch — the *logic* is proven, but that exact
syntax hasn't been run against a live ClickHouse instance yet.
