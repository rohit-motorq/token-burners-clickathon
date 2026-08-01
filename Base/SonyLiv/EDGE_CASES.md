# Edge Case Catalogue — Foreground-Only Concurrency

Every edge case we found, with measured evidence, the policy we chose, and whether the
code **provably handles it** or only documents it.

**Status legend**

| Status | Meaning |
|---|---|
| ✅ **VERIFIED** | Tested against real or injected input; the code demonstrably handles it |
| ⚠️ **GAP** | Correct on this data but unhandled for the unseen day, or deferred to ClickHouse |
| 🔵 **CLEAN** | Does not occur in this file; must still be defended (the unseen day may differ) |

Reproduce everything: `cd analysis && ./run_all.sh --fresh`.
The runner exits non-zero if any check reports FAIL.

**Current score: 16 PASS / 3 GAP / 0 FAIL** (`out_09.txt`), plus 7/7 on intra-minute
flapping (`out_10.txt`).

---

## Contents

1. [Signal interpretation](#1-signal-interpretation) — what an event actually means
2. [Counting / aggregation](#2-counting-and-aggregation) — the delta-model bugs
3. [Session lifecycle](#3-session-lifecycle) — malformed and incomplete sessions
4. [State machine](#4-state-machine) — contradictory and ambiguous ordering
5. [Time and boundaries](#5-time-and-boundaries) — minutes, hours, days
6. [Dimensions and joins](#6-dimensions-and-joins) — filters and metadata
7. [Open sessions and updates](#7-open-sessions-and-updates) — the live edge
8. [Query semantics](#8-query-semantics) — traps SQL will not catch
9. [Engine portability](#9-engine-portability) — DuckDB vs ClickHouse
10. [Synthetic-data fingerprints](#10-synthetic-data-fingerprints) — do not build on these
11. [Priority summary](#11-priority-summary)

---

## 1. Signal interpretation

### 1.1 Heartbeats continue while the app is backgrounded ✅ VERIFIED

**The single most important case in this document.** The intuitive shortcut — "a heartbeat
proves the user is watching" — is false.

| Evidence | Value |
|---|---|
| Signals firing strictly inside a background window | **3,674** |
| Sessions affected | **2,361** |
| `pause` markers among them | 1,657 |
| `dropped-frames` occurrences while backgrounded | 13.8% |
| `network-change` while backgrounded | 25.3% |
| `download_completed` while backgrounded | 26.0% |

Audio-only playback continues off-screen and the app keeps reporting.

**Policy:** foreground state takes precedence over heartbeat presence. A heartbeat proves
the process is *alive*, never that it is *visible*.

**Consequence if ignored:** all 31% of background wall-clock time flows straight back into
the metric, defeating the entire purpose of the exercise.

*Evidence: `out_07.txt`, `out_04.txt`*

---

### 1.2 Pause/resume markers are hidden inside `VideoHeartbeat` ✅ VERIFIED

There is no `VideoPause` event type. The playback state markers live in the `event`
column of `VideoHeartbeat` rows:

```
event_type = "VideoHeartbeat"    ← what you filter on
event      = "pause"             ← where the state change actually is
```

`VideoHeartbeat` is 93% of all rows and carries **41 distinct `event` values** meaning
completely different things.

**Policy:** normalise on `(event_type, event)` together, never `event_type` alone. Include
the variants: `speed-pause`/`speed-resume`, `AdPause`/`AdResume`.

**Consequence if ignored:** every pause in the dataset is missed **silently** — no error,
just a wrong number.

*Evidence: `out_01.txt`, `out_07.txt`*

---

### 1.3 Heartbeat cadence is 40s, not the documented 60s ✅ VERIFIED

The data dictionary states heartbeats arrive "every 1 minute". Measured:

| Event | p25 gap | p50 gap | p75 gap | IQR/median |
|---|---|---|---|---|
| `buffer-health` | 40s | 40s | 40s | **0.00** |
| `video-resize` | 40s | 40s | 40s | **0.00** |
| `network-bandwidth` | 40s | 40s | 40s | **0.00** |
| `network-activity` | 32s | 40s | 40s | 0.20 |

An IQR/median of exactly zero is a machine-precise timer.

**Policy:** liveness timeout = 90s (≈2 missed beats). Any threshold derived from a 60s
assumption is calibrated against a cadence that does not exist.

**Sensitivity:** sweeping 45s–300s moves peak by under 0.5%, so the choice is safe — but
90s is the principled one rather than a fitted one.

*Evidence: `out_07.txt`, `out_02.txt`*

---

### 1.4 No single heartbeat family can carry liveness ✅ VERIFIED

| Liveness basis | Sessions covered | Sessions **missed** |
|---|---|---|
| `network-activity` alone | 9,630 | 1,236 |
| `buffer-health` alone | 9,582 | 1,284 |
| `video-resize` alone | 7,791 | 3,075 |
| Union of all three | 9,708 | **1,158** |
| **Any signal at all** | **10,866** | **0** |

Even the union of all three keepalive families misses 1,158 sessions (10.7%).

**Policy:** the liveness gate keys off **any** signal in the timeline, not a designated
heartbeat event.

*Evidence: `out_07.txt`*

---

### 1.5 `BufferStart`/`BufferEnd` are not inactivity ✅ VERIFIED

The opposite trap to 1.1. These *look* like the user stopped watching, but buffering
mid-playback **is** watching.

| Evidence | Value |
|---|---|
| `BufferStart` occurrences | 66,641 across 56.2% of sessions |
| p50 inter-arrival | 2.8s (event-driven, not periodic) |
| Fires while backgrounded | only 0.21% |

**Policy:** treat as liveness evidence only. Never as a stop signal.

**Consequence if ignored:** nearly every session fragments and concurrency undercounts.

*Evidence: `out_07.txt`*

---

### 1.6 Download events are playback-independent ✅ VERIFIED

Offline downloads proceed with no playback at all, and often while backgrounded.

| Event | Occurrences | While backgrounded |
|---|---|---|
| `download_asset_played` | 1,154 | 0% |
| `download_initiated` | 409 | 9.5% |
| `download_completed` | 362 | **26.0%** |

**Policy:** a download event must never open an active interval on its own.

*Evidence: `out_07.txt`*

---

### 1.7 UX events imply foreground (unused, but quantified) ⚠️ GAP

A user cannot seek an invisible player. Where a UX event fires while carried state says
"background", the `AppForegrounded` marker was dropped — the dictionary warns these events
are "not guaranteed".

| Evidence | Value |
|---|---|
| UX events firing while state says background | **45** |
| Sessions affected | **15** |

**Policy:** *not* used in the current definition — the volume is too small to justify the
extra complexity. Documented so the omission is a decision, not an oversight. Revisit if
the unseen day shows more dropped `FG` markers.

*Evidence: `out_07.txt`*

---

## 2. Counting and aggregation

### 2.1 Per-interval deltas double-count a session ✅ VERIFIED

**The highest-impact bug in the whole model.** Concurrency counts *distinct sessions*, not
intervals. A session with several active intervals inside one minute gets counted once per
interval.

```
Minute 10:   ├─ active ─┤  (pause)  ├─ active ─┤
              +1     −1             +1      −1
             
Naive sum → 2 viewers.   Truth → 1 viewer.
```

| Evidence | Value |
|---|---|
| Spurious `+1`s in this dataset | **4,854** |
| Peak reported by the naive model | **2,902** |
| True peak | **2,697** |
| Overcount | **7.6%** |
| Intervals → merged minute-runs | 32,122 → 16,518 |

**Policy:** three steps — explode intervals to minutes, **dedupe to distinct
`(session, minute)`**, merge contiguous minutes into runs. Emit deltas per *run*.

*Evidence: `out_03.txt`, `out_10.txt`*

---

### 2.2 Intra-minute flapping — the worst case for 2.1 ✅ VERIFIED

Two patterns cause a session to appear multiple times in a single minute. Both tested with
injected fixtures:

| Injected pattern | Intervals | Minutes occupied | Result |
|---|---|---|---|
| `BG → FG` inside one minute | 2 | `[0]` | counted once ✓ |
| `pause → play → pause → play` | 3 | `[0]` | counted once ✓ |
| 10 rapid `pause/resume` flips | 6 | `[0]` | counted once ✓ |
| `BG` in minute A, `FG` in minute B | 2 | `[0, 1]` | once in **each** ✓ |
| Minute entirely backgrounded | 2 | `[0, 2]` | **minute 1 absent** ✓ |
| 200ms sliver of activity | 1 | `[0]` | still occupies it ✓ |
| `BG` + `FG` in the **same millisecond** | 1 | `[0]` | zero-length gap collapsed ✓ |

On those 7 fixtures the naive model reports **15 viewers where there are 7** (2.1x). The
cross-boundary cases are unaffected, which pins the bug precisely: it bites only when
multiple intervals share a minute.

**Frequency in real data — this is not exotic:**

| Evidence | Value |
|---|---|
| Session-minutes containing >1 active interval | **3,476 of 27,268 (12.75%)** |
| Worst case in one minute | **21 intervals** |

**Which pattern causes it:**

| Cause | Flapping minutes |
|---|---|
| both BG/FG **and** pause/resume churn | 1,998 |
| pause/resume churn only | 1,465 |
| **BG/FG churn only** | **13** |

Pause/resume churn dominates. Pure BG/FG churn is rare because backgrounding usually
persists past a minute boundary, appearing as a gap *between* minutes rather than churn
*within* one.

*Evidence: `out_10.txt`*

---

### 2.3 The delta table is sparse — a plain cumulative sum skips minutes ✅ VERIFIED

Only minutes where something changed get a row.

| Evidence | Value |
|---|---|
| Minutes carrying a delta row | **1,490** |
| Minutes actually occupied | **3,649** |
| Non-empty minutes reported by the naive sum | 1,835 (vs 3,649) |

```
Sparse (wrong):   min 0: +5  │   —   │   —   │ min 3: −2
                     5                            3      ← minutes 1,2 missing

Densified (right): min 0: +5 │ min 1 │ min 2 │ min 3: −2
                     5           5       5        3      ✓
```

**Policy:** the serving query must densify the minute axis over the requested window and
carry the running sum across gaps. ClickHouse:
`ORDER BY minute WITH FILL STEP toIntervalMinute(1)`.

**Verification (asserted, not eyeballed):**

```
ASSERTION global:    minute-run delta + dense fill == brute force -> PASS
ASSERTION per-slice: 0 mismatching group-minutes                  -> PASS
```

Peak 2,697 and average 34.8476 match brute-force overlap exactly, globally **and** per
platform × content combination.

*Evidence: `out_03.txt`*

---

### 2.4 Duplicate event rows ✅ VERIFIED

| Evidence | Value |
|---|---|
| Duplicate groups | **3,413** |
| Excess rows | **4,210** (0.465% of all rows) |
| Duplicate `START` / `END` / `PLAY` | 13 / 14 / 16 sessions |
| Peak with duplicates vs deduplicated | **identical (2,697)** |

Injected test: a session with **every** event duplicated produced 1 interval / 59s.

**Why it still matters despite passing:** the interval builder absorbs duplicates because
`min(START)`, `max(END)` and state carry-forward are idempotent. But **deltas are
additive** — a duplicated `+1` with no matching `−1` in the ClickHouse layer corrupts every
subsequent minute *permanently*. This is strictly worse than in an
idempotent-overwrite model.

**Policy:** dedupe on `(session, event_type, event, timestamp)` before the delta layer.
Use `insert_deduplication_token` on the sink.

> ⚠️ **Trap:** do not use `ReplacingMergeTree` + a materialized view for this. The MV fires
> on *insert*, not on merge-time replacement, so it sees both copies and the dedup never
> reaches your aggregate.

*Evidence: `out_04.txt`, `out_09.txt`*

---

## 3. Session lifecycle

### 3.1 Sessions with no `VideoSessionEnd` (still open) ✅ VERIFIED / 🔵 CLEAN in file

**Not present in this file at all** — all 10,866 sessions have both a start and an end. The
problem statement promises open sessions on the unseen day, so this had to be injected.

Injected test: session with no `END` produced **79s across 1 interval**, closed at the last
observed signal.

Three candidate policies, measured at a watermark 25 min before the last event
(3,338 sessions open):

| Policy | Concurrency at watermark minute | Peak | Verdict |
|---|---|---|---|
| P1 drop open sessions | 4 | 1,437 | erases the live edge |
| **P2 active until last signal** | **430** | **2,695** | **chosen** |
| P3 assume active to watermark | 2,087 | 2,697 | assumes silence = watching |

**Policy:** P2 + the liveness gate — active until proven stale.

All 3,338 open sessions did in fact signal again, so P3's optimism happens to be right
here. It is right *by luck*, and it is the policy a genuine crash-and-abandon breaks.

*Evidence: `out_05.txt`, `out_09.txt`*

---

### 3.2 Missing `VideoSessionStart` ✅ VERIFIED (bug found and fixed)

🔵 Does not occur in this file — all 10,866 sessions have a `START`.

Injected test: produced **60s across 1 interval**.

**This case exposed a real bug.** See [§9.1](#91-greatest-null-semantics-differ-between-duckdb-and-clickhouse-verified-bug-found-and-fixed) — it appeared to work only because
of DuckDB NULL semantics, and would have erased the session in ClickHouse.

**Policy:** explicit `coalesce(start_ms, first_ts)`. A lost or late `START` must never
erase a session from concurrency.

*Evidence: `out_09.txt`*

---

### 3.3 Sessions that never play ✅ VERIFIED

Injected tests:

| Case | Active time | Correct? |
|---|---|---|
| `START` → `HB` → `END`, no `PLAY` | **0s** | ✓ a session that never rendered a frame |
| Heartbeats only, no lifecycle events | **0s** | ✓ heartbeats alone cannot manufacture viewing |

| Real data | Value |
|---|---|
| Sessions with no `PLAY` | 0 |
| Sessions ending with zero active time | **16** |

**Policy:** the playing gate defaults to closed, so no `PLAY` means no active time. Such
sessions must not be counted at all.

*Evidence: `out_04.txt`, `out_09.txt`*

---

### 3.4 Zero-length and sub-second sessions ✅ VERIFIED

| Evidence | Value |
|---|---|
| Zero-length sessions | 0 |
| Under 1 second | 12 |
| Under 1 minute | 978 |
| Negative duration | 0 |
| Zero-length intervals **emitted** | **0** |

**Policy:** half-open intervals `[a, z)` plus `HAVING max(z_ms) > min(a_ms)` drop
zero-length stretches automatically.

A 200ms session **does** occupy a full minute for peak purposes (it was genuinely
concurrent), which inflates minute-count averages — see [§8.2](#82-average-requires-a-declared-denominator-verified).

*Evidence: `out_04.txt`, `out_09.txt`, `out_10.txt`*

---

### 3.5 `END` timestamp before `START` (negative duration) ✅ VERIFIED

🔵 Does not occur in this file (0 negative durations).

Injected test: produced **0s / 0 intervals** — the inverted window is rejected rather than
emitting a negative interval.

**Policy:** `WHERE ts <= eff_end_ms` plus `seg_end > ts` reject it structurally.

*Evidence: `out_09.txt`*

---

### 3.6 Marathon and abandoned-but-open sessions ✅ VERIFIED

| Evidence | Value |
|---|---|
| Sessions over 1 hour | 150 |
| Over 6 hours | 12 |
| Over 12 hours | 1 |
| **Longest session** | **43.6 hours** |

Injected test: a 10-hour session with a silent gap produced **91s**, correctly clipped.

**Retention by archetype** — the liveness gate matters enormously here:

| Shape | Sessions | Wall-clock h | Active h | Retained |
|---|---|---|---|---|
| single background excursion | 8,214 | 1,961.7 | 1,258.0 | 64.1% |
| multi excursion (2+) | 2,034 | 748.9 | 431.8 | 57.7% |
| ended backgrounded | 313 | 63.5 | 31.4 | 49.4% |
| errored | 293 | 64.8 | 37.1 | 57.3% |
| **marathon (>6h)** | 12 | 133.5 | 4.9 | **3.7%** |

**Policy:** the 90s liveness cap bounds these. Without it, one abandoned phone contributes
43 hours of phantom concurrency.

**Consequence:** a **17x spread** in retention means no single global correction factor can
be right. See [§8.4](#84-no-blanket-correction-factor-verified).

*Evidence: `out_04.txt`, `out_08.txt`, `out_09.txt`*

---

### 3.7 Long signal gaps inside live sessions ✅ VERIFIED

| Gap threshold | Occurrences |
|---|---|
| over 45s | 9,026 |
| over 90s | **5,677** |
| over 5 min | 2,445 |
| over 30 min | 206 |
| **maximum gap** | **39.6 hours** |

**Policy:** cap each segment at 90s past its opening signal. The gate clips silence rather
than dropping the session entirely, so a session that returns is still counted before and
after.

*Evidence: `out_04.txt`*

---

## 4. State machine

### 4.1 Unbalanced background/foreground markers ✅ VERIFIED

The dictionary warns `AppBackgrounded`/`AppForegrounded` are "not guaranteed".

| Evidence | Value |
|---|---|
| More `BG` than `FG` (unclosed background) | **418** |
| More `FG` than `BG` | 48 |
| Balanced | 10,400 |
| No state markers at all | 0 (in this file) |

Injected test: backgrounded-and-never-returns produced **29s of a 600s session** — the
9.5-minute tail correctly excluded.

**Policy:** carry state forward from the last known marker and clamp at session end. An
unclosed `BG` means the remainder is inactive. **Never** read a missing `FG` as foreground.

*Evidence: `out_04.txt`, `out_09.txt`*

---

### 4.2 Session's last state marker is `BG` (never returned) ✅ VERIFIED

| Evidence | Value |
|---|---|
| Sessions whose final state marker is `BG` | **344** |
| Wall-clock hours in those tails | **6.72 h** |
| (For contrast) sessions ending on `FG` | 10,522 — 770.22 h |

**Policy:** exclude the tail after a final `BG`.

*Evidence: `out_04.txt`*

---

### 4.3 Contradictory and redundant transitions ✅ VERIFIED

Real transitions found in the data:

| Transition | Occurrences | Why it is odd |
|---|---|---|
| `RESUME → RESUME` | 1,778 | resume without an intervening pause |
| **`END → BG`** | **213** | **signals after the session declared itself over** |
| `PAUSE → PAUSE` | 189 | redundant |
| `BG → BG` | 109 | redundant |
| `FG → FG` | 45 | redundant |
| `END → PLAY` | 15 | playback after end |
| `START → START` | 14 | duplicate open |
| `END → END` | 12 | duplicate close |

**Policy:** idempotent state carry-forward handles all of these for free — a second `BG`
changes nothing. The `END → *` family is the dangerous one; see [§5.1](#51-events-arriving-after-videosessionend-verified).

**Why carry-forward rather than pattern matching:** the state-only sequence (heartbeats
removed) contains **2,655 distinct paths** across 10,866 sessions, and the most common
covers only 10.8%. Enumerating rules over these shapes does not generalise.

*Evidence: `out_04.txt`, `out_08.txt`*

---

### 4.4 Same-millisecond signal collisions ✅ VERIFIED

| Evidence | Value |
|---|---|
| Session-milliseconds with >1 signal | **161,660** |
| Collisions with **differing** signal kinds | **6,058** |
| Max signals in one millisecond | 12 |

Injected test: `BG` + `FG` + `pause` + `resume` all in the same millisecond produced
**1 interval, deterministically**.

Verification: rebuilding the identical definition twice yields **0 differing intervals**.

**Policy:** fixed tie-break priority —
`START(0) → PLAY(1) → FG(2) → RESUME(3) → HB(4) → ERR(5) → PAUSE(6) → BG(7) → END(9)`.
Openers resolve before closers so a session cannot close before it opens.

**Consequence if ignored:** the same input produces different concurrency on different
runs. Non-reproducible benchmark answers.

*Evidence: `out_04.txt`, `out_09.txt`*

---

### 4.5 Events out of order within a session ✅ VERIFIED

| Evidence | Value |
|---|---|
| Intra-session steps out of order | **11.35%** (101,534 of 894,692) |
| Max inversion | 155,604s (~43 h) |

**Policy:** the interval builder must `ORDER BY` event timestamp explicitly and never trust
insertion or file order for state carry-forward.

*Evidence: `out_05.txt`*

---

### 4.6 `VideoError` does not necessarily stop playback ✅ VERIFIED

| Evidence | Value |
|---|---|
| Sessions with an error | 293 |
| Error → immediately `END` | **238** |
| Error → session **continues** | **55** |

**Policy:** do **not** treat `ERR` as an independent stop signal. 238 of 293 are already
handled by the `END` clamp, and killing the other 55 would undercount sessions that
recovered and kept playing.

*Evidence: `out_04.txt`*

---

### 4.7 Background and pause excursion shapes ✅ VERIFIED

Understanding these explains why one session yields multiple intervals.

| Excursion type | Count | p50 length | p90 length | Over 5 min |
|---|---|---|---|---|
| Background | **14,247** | 35.1s | 509s | 2,208 |
| Pause | **21,128** | 20.5s | 285.2s | 2,017 |

Background excursions under 5s: 3,504. Between 5s and 1min: 4,847.

**The gates are correlated in sequence, not nested in time.** `PAUSE → BG → FG` is the
single most common trigram (10,994 occurrences, 11.0%), yet only **11.35% of background
windows actually contain** a pause marker. The client pauses *just before* backgrounding.

**Consequence:** the foreground and playing gates exclude overlapping time, so their
savings are **not additive**. Attribution must report combined reasons on their own rows:

| Exclusion reason | Segments | Wall-clock hours |
|---|---|---|
| ACTIVE (nothing excluded) | 752,050 | 1,755.3 |
| paused only | 122,378 | 245.9 |
| **all three gates** | 4,814 | **839.0** |
| background + paused | 11,200 | 57.1 |
| paused + stale | 438 | 31.8 |
| stale only | 316 | 28.3 |
| background + stale | 97 | 12.7 |
| background only | 2,582 | 2.3 |

Two very different failure modes: the largest excluded block (839 h) is only 4,814
segments — abandoned sessions left backgrounded. The most *numerous* (122,378 segments) is
ordinary users pausing. A naive model conflates them.

*Evidence: `out_08.txt`*

---

### 4.8 Default state before any marker exists ✅ VERIFIED

The highest-leverage judgement call in the entire model.

| Gate default | Time it decides | Choice |
|---|---|---|
| Playing, before first `PLAY` (p50 2.8s) | 31.4 h | `playing = 0` → exclude |
| **Foreground, before first BG/FG (p50 186s)** | **1,124.6 h** | **`foreground = 1` → include** |

The foreground default alone decides **over a third of all wall-clock time in the dataset**.

**The naive read argues for the wrong answer.** 99.7% of sessions (10,837 of 10,866) open
their state history with `AppBackgrounded`, which looks like sessions start hidden.

**The sequence disproves it:**

| Evidence | Value |
|---|---|
| First `BG` arrives **after** the first `PLAY` | **96.98%** (10,538 of 10,866) |
| Modal opening signature | `START → PLAY → HB → HB` (**72.9%**) |
| `START → PLAY` share of all transitions out of `START` | 92.87% |

The `BG` marker is the user **leaving** a demonstrably visible session, not evidence it
began hidden.

**Consequence if reversed:** the first ~3 minutes (p50 186s) of *every* session is erased.

Injected test: a session with no BG/FG markers at all produced **119s** — correctly
counted, because the default is foreground.

*Evidence: `out_08.txt`, `out_09.txt`*

---

## 5. Time and boundaries

### 5.1 Events arriving after `VideoSessionEnd` ✅ VERIFIED

| Evidence | Value |
|---|---|
| Events after the `END` timestamp | **802** |
| Sessions affected | **239** |
| Max lateness past end | **34.68 min** |
| Intervals extending past session end | **0** |

**Policy:** clamp all activity to `<= session_end`. An `END` event is an explicit statement
that playback stopped; trailing heartbeats are flush-on-exit retries, not viewing.

*Evidence: `out_04.txt`, `out_09.txt`*

---

### 5.2 Sessions crossing minute, hour, and day boundaries ✅ VERIFIED

| Boundary | Sessions crossing |
|---|---|
| Minute | **10,438** (96% of all sessions) |
| Hour | **3,882** |
| UTC day | **11** |

Injected test: `BG` in minute A, `FG` in minute B produced intervals in **both** minutes,
counted once in each.

**Policy:** a session must be counted in **every** minute it spans, so an interval cannot
be attributed to a single bucket. Day-partitioned tables need cross-partition reads, or
deltas emitted into each partition they touch.

*Evidence: `out_04.txt`, `out_10.txt`*

---

### 5.3 A minute entirely inside a background window ✅ VERIFIED

Injected test: session active in minute 0, backgrounded through all of minute 1, returns in
minute 2. Result: occupied minutes `[0, 2]` — **minute 1 correctly absent**.

**Policy:** falls out of the occupancy model naturally; no special case needed. Worth
testing explicitly because an off-by-one in the explode step would silently fill it.

*Evidence: `out_10.txt`*

---

### 5.4 Finalisation cannot use a fixed delay ⚠️ GAP (design decision)

A minute is final only when every session overlapping it has closed.

| Session duration percentile | Value |
|---|---|
| p50 | 11.9 min |
| p95 | 41.5 min |
| p99 | 74.1 min |
| p99.9 | 374.6 min |
| **max** | **2,618 min (43.6 h)** |

**Policy:** a "finalise after N minutes" rule is wrong for that tail. Watermark-finalise
the bulk and keep a small open-session overlay for the long tail.

**Related GAP:** the design specifies cutting runs at hour boundaries to bound the hot
path. Not implemented in the analysis code.

> ⚠️ Run segmentation is **concurrency-neutral** (`+1@11:00` and `−1@11:00` cancel), so it
> **cannot be validated by comparing peaks**. It needs its own dedicated test, or it will
> appear to work while doing nothing.

*Evidence: `out_05.txt`*

---

### 5.5 Arrival lateness is not measurable in this dataset ⚠️ GAP

| Evidence | Value |
|---|---|
| Ingestion/received timestamp column | **absent** |
| Sessions stored contiguously in the file | **96.6%** |
| Adjacent rows going backwards in time | 7.86% |

The file is grouped **by session**, not ordered by time. Any lateness figure derived from
row order is an artifact of that grouping, not a real measurement.

**Policy:** build nothing that depends on input file order. Have the pipeline stamp its own
arrival time (`DEFAULT now()`) so lag becomes observable on the unseen day. That
ingestion-lag metric is also the natural ClickStack integration.

**Silver lining:** late data is genuinely cheap in a delta model — a late event is just more
deltas, appended. No read-modify-write, no re-reading history. This is the delta model's
biggest advantage over a mutable per-session row.

*Evidence: `out_05.txt`*

---

## 6. Dimensions and joins

### 6.1 Dimension drift within a single session ✅ VERIFIED

| Dimension | Sessions with >1 value |
|---|---|
| `audio_language` | **6,864** |
| `subtitle_language` | **8,882** |
| `user_id` | 120 |
| `platform` | 95 |
| `content_id` | 1 |

**Policy:** pin every dimension to its value at the session-start event. Verified:
`session_dim` yields exactly one row per session (0 duplicates).

**Consequence if ignored:** a filtered query counts one session twice, and per-slice counts
stop summing to the total.

*Evidence: `out_01.txt`, `out_09.txt`*

---

### 6.2 Content metadata join must not drop sessions ✅ VERIFIED

| Evidence | Value |
|---|---|
| Distinct `content_id` in raw data | 3,357 |
| IDs with no metadata row | **0** |
| Content rows with blank `video_type` | **1,089** |
| IDs with blank `video_type` | 142 |
| **Sessions affected** | **250** |
| Sessions lost by our join | **0** |

**Policy:** `LEFT JOIN` + `coalesce(..., 'unknown')`. **Never `INNER JOIN`** — it would
silently delete those 250 sessions from every `video_type`-filtered answer.

*Evidence: `out_01.txt`, `out_09.txt`*

---

### 6.3 Blank and sentinel dimension values ⚠️ GAP

| Evidence | Value |
|---|---|
| `subtitle_language` in {`UNK`, `OFF`} | **802,984 rows** |
| `audio_language` = `unk`/`unknown` | 51,185 rows |
| `audio_language` blank | 1,991 |
| `subtitle_language` blank | 2,006 |
| `player_version` blank | 1,534 |
| `audio_language`: raw vs `lower/trim` distinct values | **40 vs 25** |
| `subtitle_language`: raw vs normalised | **10 vs 7** |

**Status: GAP.** `session_dim` passes these through raw. Harmless today because we do not
slice on language — but a filter on `unk` would miss `UNK`, and group-by totals would split
across case variants.

**Policy:** normalise to a single `unknown` token at ingest. Required before shipping any
language filter.

*Evidence: `out_01.txt`, `out_09.txt`*

---

### 6.4 Serving-table width tension ⚠️ GAP (design decision)

| Dimension set | Combos | Delta rows |
|---|---|---|
| global / country | 1 | 1,490 |
| platform (+country) | 10 | 2,143 |
| platform, country, video_type | 29 | 2,871 |
| `content_id` | 3,357 | **19,294** |
| platform, country, content_id, video_type | 4,317 | **22,676** |
| + app_version, audio_language | 5,025 | 24,479 |

`content_id` inflates the table **9x** and is simultaneously the highest-cardinality
dimension and one of the most-requested filters.

The cube is extremely sparse: 3,649 occupied minutes × 4,317 combos = 15.7M dense cells
against 22,676 actual rows — **0.14% density**.

**Policy:** two table widths. Narrow `(platform, country, video_type)` for dashboard
defaults, content-inclusive for drill-down. Store sparse deltas ordered by
`(dims…, minute)`; never a dense grid.

**Related GAP:** hourly absolute anchors to bound the cumulative sum are specified in the
design but not implemented — the analysis code always sums from the dataset start.

*Evidence: `out_06.txt`*

---

## 7. Open sessions and updates

### 7.1 Reconnect after a network outage ✅ VERIFIED

The scenario: a user loses internet for 4 minutes and reconnects.

```
10:00  watching normally                → counted ✓
10:01  connection drops
10:02  90s of silence → activity timer  → NOT counted ✓  (correct — not watching)
       ...session state kept alive (eviction is 10 min, not 90s)
10:05  connection returns, heartbeat    → new run opens, counted again ✓
```

**Policy: two separate timers.**

| Timer | Value | Job | Decision type |
|---|---|---|---|
| **Activity** | 90s | when we stop *counting* you | correctness |
| **Eviction** | 10 min | when we *forget* your session | memory management |

Conflating them forces a trade between correctness and state size.

**Even if eviction already happened** (gap > 10 min), nothing breaks: a fresh state entry
with the same session id produces another run. Runs are independent and deltas are
additive, so session resurrection needs no reconciliation.

**This is a common path, not an edge case:**

| Evidence | Value |
|---|---|
| Sessions with more than one run | **4,511 of 10,850 (41.6%)** |
| Max runs in one session | 8 |

It is exercised constantly rather than rotting untested.

*Evidence: `out_03.txt`, `out_05.txt`*

---

### 7.2 Incremental update cost ✅ VERIFIED

| Evidence | Value |
|---|---|
| Consecutive heartbeat steps | 772,795 |
| **Landing in the same minute (no serving row changes)** | **650,388 (84%)** |
| Advancing exactly one minute | 116,030 |
| Advancing multiple minutes | 6,377 |
| Avg minutes advanced per heartbeat | 0.221 |

**Heartbeat volume does not translate into serving-layer write volume.** This is the core
justification for the whole architecture.

The alternative — recomputing a session's minutes on every heartbeat — would touch
**~12.9M rows** across this dataset (avg 16.4 min/session × 783,648 heartbeats), worst case
2,618 minutes for a single session.

**Policy:** emit `+1` eagerly at run start, `−1` at run close. Do not wait for the session
to close, or a 43-hour session contributes nothing to history for 43 hours.

*Evidence: `out_05.txt`*

---

## 8. Query semantics

These are traps the schema cannot enforce. They must live in the API layer.

### 8.1 Peak is not additive and does not roll up ✅ VERIFIED

| Evidence | Value |
|---|---|
| Sum of per-platform peaks | **2,786** |
| True overall peak | **2,697** |
| Overstatement | **89 sessions** |

Every slice peaks at its **own** minute:

| Slice | Peak | Peak minute (UTC) |
|---|---|---|
| `ANDROID_PHONE` | 1,709 | 10:56 |
| `IPHONE` | 329 | 10:55 |
| `SONY_ANDROID_TV` | 280 | 10:53 |
| `JIO_ANDROID_TV` | 211 | 10:57 |
| `Mweb` | 67 | 11:02 |
| `vod` | 2,223 | 11:02 |
| **`live`** | 425 | **10:42** ← 20 min from `vod` |

**Policy:** peak = `max()` over minute concurrency **at the requested filter combination**.
Never summed from parts, never derived from a coarser pre-aggregate.

Peak is *invariant* to grain when asked over the same window (2,697 at minute, hour, and
day). What breaks is computing it *from* pre-aggregated coarse values — a max of hourly
averages, or an average of hourly peaks, are different and wrong numbers.

*Evidence: `out_03.txt`, `out_06.txt`*

---

### 8.2 Average requires a declared denominator ✅ VERIFIED

The same question has three defensible answers:

| Interpretation | Value |
|---|---|
| Mean over occupied minutes | **34.85** |
| Mean over every minute in span (incl. 13,379 empty) | **7.47** |
| Time-weighted (active-minutes ÷ occupied minutes) | **28.99** |

A **4.7x spread**, driven by partial-minute occupancy:

| Evidence | Value |
|---|---|
| Active intervals shorter than one minute | **16,103 of 32,122 (50.1%)** |
| Shorter than 5 seconds | 6,570 |
| Entirely within a single minute | 12,125 |

**Policy:** pick one, document it in the API contract, and state it alongside the number.
Recommend mean over occupied minutes, with the time-weighted variant available.

**This is the single most likely source of a benchmark mismatch** against a private answer
key that is otherwise correct.

*Evidence: `out_03.txt`*

---

### 8.3 Session concurrency ≠ user concurrency ✅ VERIFIED

| Evidence | Value |
|---|---|
| Peak **session** concurrency | 2,697 |
| Peak **user** concurrency | 2,629 |
| Max sessions-over-users in one minute | **82** |
| Overlapping same-user session pairs | **17,397** (61 users) |
| Max sessions for one user | 301 |

**Policy:** these are different metrics. More importantly, `countDistinct(user)` is **not
delta-summable** — a user on two devices must count once — so this model is *invalid* for
user-grain concurrency.

A user-grain serving table needs a mergeable sketch
(`AggregateFunction(uniqCombined, …)`), kept separately.

*Evidence: `out_03.txt`, `out_04.txt`*

---

### 8.4 No blanket correction factor ✅ VERIFIED

Tempting shortcut: measure that ~41% of time is removed, then multiply naive numbers by
0.59.

**Retention by session archetype:** 64.1% → 57.7% → 57.3% → 49.4% → **3.7%**
(a **17x spread**, see [§3.6](#36-marathon-and-abandoned-but-open-sessions-verified)).

**Retention by content type:**

| Video type | Naive hours | Active hours | Time removed |
|---|---|---|---|
| `live` | 400.4 | 209.5 | **47.67%** |
| `vod` | 2,490.6 | 1,502.1 | 39.69% |
| unknown | 81.4 | 51.6 | 36.55% |

**Retention by platform:** 42.08% removed on `ANDROID_PHONE` vs 29.97% on
`JIO_ANDROID_TV`.

**Policy:** never apply a single multiplier. Compute the gates per session.

*Evidence: `out_02.txt`, `out_08.txt`*

---

## 9. Engine portability

### 9.1 `greatest()` NULL semantics differ between DuckDB and ClickHouse ✅ VERIFIED (bug found and fixed)

**A real bug the audit caught.** It was hidden in two independent ways.

```
DuckDB:      greatest(5, NULL) = 5      ← silently SKIPS NULL
ClickHouse:  greatest(5, NULL) = NULL   ← PROPAGATES NULL
```

The builder used `greatest(ts, b.start_ms)`. With a NULL `start_ms` in ClickHouse the
result is NULL and **the session vanishes from concurrency entirely**.

Why it was invisible twice over:

1. Every session in this file has a `START`, so the case never fires on real data
2. DuckDB masks the NULL semantics, so it passed even when deliberately injected

**Fix:** explicit `coalesce(start_ms, first_ts)` and `coalesce(end_ms, …, last_ts)`, so
behaviour does not depend on engine NULL rules. Peak remained 2,697 after the fix (no
regression).

**The test was also strengthened:** it now asserts the explicit `coalesce` is *present in
the SQL*, not merely that output appeared. Otherwise the test would keep passing for the
wrong reason.

**Broader lesson:** DuckDB agreement does not guarantee ClickHouse agreement. This audit
must be re-run against the real engine before trusting the port.

*Evidence: `out_09.txt`*

---

### 9.2 Deferred to the ClickHouse layer ⚠️ GAP

Three items are specified in the design but not present in the analysis code, because they
are serving concerns rather than correctness-of-definition concerns:

| Item | Why it is not in the analysis code | Risk |
|---|---|---|
| Hour-boundary run segmentation | Bounds the hot path; concurrency-neutral | **Cannot be validated by comparing peaks** — needs its own test |
| Hourly absolute anchors | Bounds the cumulative sum | Analysis always sums from dataset start |
| Sentinel normalisation | See [§6.3](#63-blank-and-sentinel-dimension-values-gap) | Breaks language filters |

*Evidence: `out_09.txt`*

---

## 10. Synthetic-data fingerprints

Properties this file has that look like invariants but are **artifacts of the generator**.
Building on them will break on the unseen day.

| Fingerprint | Value in this file | Why it is dangerous |
|---|---|---|
| **Every session has an `END`** | `sessions_without_end = 0` | The problem statement *promises* open sessions. Build and test that path. |
| **Every session backgrounds at least once** | archetype "never backgrounded" = **0 sessions** | Code must handle a session with no state markers at all. |
| Every session has both BG **and** FG events | 10,866 / 10,866 | Do not assume markers exist. |
| `country` has exactly one value | `india` only | Still model it as a real dimension; do not optimise it away. |
| `subtitle_language` has 2 effective values | mostly `UNK`/`OFF` | Cardinality conclusions do not transfer. |
| `session_start_epoch` always matches `VideoSessionStart` | 10,866 agree, 0 disagree | Convenient (usable as a sort key on every row) but verify on new data. |
| No nulls in any key column | 0 | Defend anyway. |
| Content metadata covers 100% of referenced IDs | 0 missing | Keep the `LEFT JOIN`. |
| **Load concentrated in one day** | 849,888 of 905,558 events on 2026-07-26 | Peak sits in a ~40-min window (10:42–11:18). A design that only performs on sparse data will look fine here. |

Both of the first two were only verified because they were **deliberately injected** —
they are untestable against this file.

*Evidence: `out_01.txt`, `out_05.txt`, `out_08.txt`*

---

## 11. Priority summary

### Correctness-critical — a wrong number if missed

| # | Case | Impact if ignored |
|---|---|---|
| 1.1 | Heartbeats fire while backgrounded | 31% of wall-clock leaks back in |
| 1.2 | Pause hidden in `VideoHeartbeat` | every pause missed, silently |
| 2.1 | Per-interval deltas double-count | peak 2,902 vs 2,697 (**+7.6%**) |
| 2.2 | Intra-minute flapping | 12.75% of session-minutes affected |
| 2.3 | Sparse delta table skips minutes | concurrency in the wrong minutes |
| 4.8 | Foreground default | **1,125 h** — a third of the dataset |
| 8.1 | Peak summed across slices | 2,786 vs 2,697 |
| 8.2 | Undeclared average | 4.7x spread between valid answers |

### Robustness — silent data loss or non-reproducibility

| # | Case | Impact |
|---|---|---|
| 2.4 | Duplicates in the delta layer | **permanent** corruption of all later minutes |
| 3.2 / 9.1 | Missing `START` + NULL semantics | entire sessions vanish |
| 4.4 | Same-ms collisions | non-reproducible answers |
| 4.5 | Out-of-order events (11.35%) | wrong state carry-forward |
| 6.2 | `INNER JOIN` on metadata | 250 sessions silently deleted |
| 3.6 | Marathon sessions | 43 h of phantom concurrency |

### Open decisions

| # | Case | Needed before |
|---|---|---|
| 6.3 | Sentinel normalisation | shipping any language filter |
| 5.4 | Hour-boundary segmentation | production hot path (**needs its own test**) |
| 6.4 | Hourly anchors | querying historical ranges at scale |
| 1.7 | UX-as-foreground repair | only if the unseen day drops more `FG` markers |

---

## Where each figure comes from

| Output | Contents |
|---|---|
| `out_01.txt` | Volume, event mix, cardinality, nulls, timestamp sanity, content join |
| `out_02.txt` | Gate-by-gate impact, liveness timeout sweep, per-dimension divergence |
| `out_03.txt` | **Delta model vs brute force (asserted)**, peak/average semantics, run structure |
| `out_04.txt` | 18-case census with a policy per case |
| `out_05.txt` | Open-session policies, incrementality, finalisation, ordering |
| `out_06.txt` | Filter space, serving cardinality by dimension set, scale ratios |
| `out_07.txt` | Heartbeat taxonomy: periodicity, background/pause rates, liveness coverage |
| `out_08.txt` | Transition matrix, signatures, trigrams, archetypes, gate decision table |
| `out_09.txt` | **Edge case audit** — 16 PASS / 3 GAP / 0 FAIL, 10 injected pathologies |
| `out_10.txt` | **Intra-minute flapping** — 7 injected fixtures + real-data frequency |

### Scope and caveats

- **Validated in DuckDB, not ClickHouse.** [§9.1](#91-greatest-null-semantics-differ-between-duckdb-and-clickhouse-verified-bug-found-and-fixed) is direct
  evidence that DuckDB agreement is insufficient. Re-run this audit against the real engine.
- **Figures are for the V3 definition** (foreground + playing + 90s liveness). A different
  definition yields different numbers; [§8.4](#84-no-blanket-correction-factor-verified) quantifies the spread.
- **No latency measurements here.** Query performance must be measured on ClickHouse Cloud
  at the real data volume; DuckDB timings on a local file would not be meaningful.
- **Scale figures are ratio-based extrapolation**, not a benchmark. Dimension combos grow
  with volume, so delta rows will not scale exactly linearly.
