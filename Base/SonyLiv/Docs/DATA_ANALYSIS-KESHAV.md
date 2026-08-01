# Data Analysis Report — SonyLIV Foreground-Only Concurrency

Analysis of `ch-hackathon-raw-data.csv` (905,558 events) and
`ch-hackathon-content-data.csv` (33,464 rows), oriented at the problem statement in
`problem.md`.

Everything below is reproducible: `analysis/run_all.sh --fresh` reloads the CSVs and
regenerates `analysis/out_0*.txt`. Two correctness claims are machine-asserted rather
than asserted in prose.

---

## 1. Headline findings

Six things change how the system should be built.

**1. The data dictionary's heartbeat interval is wrong.** It states heartbeats arrive
"every 1 minute". The measured cadence for the periodic heartbeat families
(`network-activity`, `buffer-health`, `video-resize`) is **40 seconds** at p10, p50, and
p90 alike. Any liveness threshold tuned to a 60s assumption is calibrated against a
cadence that does not exist. We use 90s (~2 missed 40s beats).

**2. `VideoHeartbeat` is not one signal, it is 41.** The event types are as documented,
but 93% of all rows are `VideoHeartbeat`, and its `event` column carries 41 distinct
values that mean completely different things: `pause`, `resume`, `Seek`, `BufferStart`,
`AdPause`, `download_completed`, `network-bandwidth`. **The playback pause/resume state
markers are hidden inside the heartbeat event type, not in a separate event type.** A
model that treats `event_type='VideoHeartbeat'` as "user is watching" both misses the
pause signal and counts paused time as active.

**3. Heartbeats continue during backgrounded periods.** 3,674 signals across 2,361
sessions occur strictly inside a background window, including 1,657 `pause` markers and
291 `network-activity` beats. This kills the tempting shortcut "a heartbeat proves
foreground". Foreground state must take precedence over heartbeat presence, or
background time leaks straight back into the metric.

**4. Background time is 31% of wall-clock, and the gates together remove 41%.** Naive
session-overlap gives 2,972 active hours and a peak of 3,739. Foreground-only gating
brings it to 2,061 hours. Adding the playing and liveness gates lands at **1,763 hours
and a peak of 2,697**. The naive peak is **38.6% above the correct answer** — 27.9% of it
is phantom. This is the error the problem exists to prevent, and it is not uniform: it is
**48% for `live` content** versus 40% for `vod`, and 42% on `ANDROID_PHONE` versus 30%
on `JIO_ANDROID_TV`. Any single global correction factor would be wrong per slice.

**5. The obvious interval-to-delta model is subtly wrong (two ways).** The problem
statement suggests "+1 at start, −1 at end, cumulative sum". Implemented literally on
active intervals, it reports a peak of **2,902 against a true 2,697** — it overcounts
by 7.6%. Two independent bugs, both detailed in §3.

**6. Only one calendar day carries real load.** 849,888 of 905,558 events and 10,524 of
10,866 sessions land on 2026-07-26, with the peak in a ~40-minute window around
10:42–11:18 UTC. The other six days are a sparse trickle (2 to 204 sessions). The file
is a live-event spike with a thin tail, so benchmark latency will be dominated by that
one narrow window, and a design that only performs well on sparse data will not show it
here.

---

## 2. Defining the active interval

The phrase "truly active" resolves into three independent gates. We derive intervals by
carrying state forward over a per-session signal timeline and emitting half-open
intervals `[a, z)`. Sections 2a and 2b establish the evidence for these choices.

| Gate | Signals | What it removes |
|---|---|---|
| Foreground | `AppBackgrounded` / `AppForegrounded` | app backgrounded |
| Playing | `VideoPlay`, `event='pause'` / `'resume'` (+ `speed-*`, `Ad*` variants) | paused playback |
| Liveness | any signal freshness, 90s cap | died without saying so |

**The final rule, stated once:**

> A session occupies minute `M` iff there exists an instant `t ∈ [M, M+1)` where all hold:
> 1. **Foreground** — most recent BG/FG marker at or before `t` is `AppForegrounded`
>    (default before any marker: foreground)
> 2. **Playing** — most recent `VideoPlay`/`resume`/`pause` marker is play or resume
>    (default before any marker: not playing)
> 3. **Fresh** — `t` is within 90s of the signal opening the current segment (any signal)
> 4. **In bounds** — `session_start ≤ t ≤ session_end`
>
> `Concurrency(M)` = count of **distinct sessions** satisfying this at `M`.
> `Peak(window)` = `max` over `M`. Never summed across slices, never derived from a
> coarser pre-aggregate.

Measured effect of each gate (`out_02.txt`):

| Definition | Intervals | Active hours | % of naive | Peak | Occupied minutes |
|---|---|---|---|---|---|
| V0 session start→end | 10,869 | 2,972.4 | 100% | 3,739 | 5,255 |
| V1 + foreground | 25,087 | 2,061.4 | 69.4% | 3,009 | 3,873 |
| V2 + playing | 31,888 | 1,783.7 | 60.0% | 2,719 | 3,649 |
| **V3 + 90s liveness** | **32,122** | **1,763.2** | **59.3%** | **2,697** | **3,649** |

The liveness gate contributes little total time (0.7%) but it is not optional: it is the
only thing bounding the pathological sessions. One session spans **43.6 hours** and 12
exceed 6 hours. Without a liveness cap, a single abandoned-but-open session contributes
43 hours of phantom concurrency. Measured per archetype (§7a), marathon sessions retain
only **3.7%** of their wall-clock time against 64% for a normal session — a 17x spread.

**Threshold choice.** Sweeping the timeout (45s/60s/90s/120s/300s) moves peak by under
0.5%. The metric is not sensitive here, so 90s is chosen on cadence reasoning (2 missed
40s beats), not curve-fitting. That insensitivity is worth knowing: it means the gate is
safe, not that it is unnecessary.

### The two default-state decisions

These decide time for which *no marker exists*, and they are the highest-leverage
judgement calls in the model.

| Gate default | Time it decides | Choice |
|---|---|---|
| Playing, before first `VideoPlay` (p50 2.8s) | 31.4 h | `playing=0` → exclude; the player is starting up |
| Foreground, before first BG/FG marker (p50 186s) | **1,124.6 h** | `foreground=1` → include |

The foreground default alone decides **1,125 hours, over a third of all wall-clock time
in the dataset**. And the naive read of the data argues for the wrong answer: **99.7% of
sessions (10,837 of 10,866) open their state history with `AppBackgrounded`**, which looks
like sessions start hidden.

The sequencing disproves it. **97% of those first `AppBackgrounded` markers arrive *after*
the first `VideoPlay`**, and the modal opening is `START → PLAY → HB → HB` (72.9% of
sessions). The BG marker is the user *leaving* a visible session, not evidence it began
hidden. Defaulting to background would erase the first ~3 minutes of every session.

This is the clearest example of why sequence analysis, not just event counting, is needed
to define "active" correctly.

---

---

## 2a. Heartbeat taxonomy — what the 41 sub-events actually mean

`out_07.txt`. Classification is empirical, not name-guessing, using four measured
signals: inter-arrival periodicity (IQR/median — robust to the multi-hour gaps that
would wreck a stddev-based CV), rate of firing while backgrounded, rate while paused,
and session coverage.

| Class | Distinct events | Events | % of heartbeats | Role in the active definition |
|---|---|---|---|---|
| KEEPALIVE | 5 | 527,921 | 62.6% | Liveness evidence **only** |
| QUALITY | 7 | 161,345 | 19.1% | Liveness evidence only |
| UX | 12 | 90,241 | 10.7% | Liveness + implies foreground |
| STATE | 6 | 59,952 | 7.1% | **Changes the playing gate** |
| AD | 5 | 2,190 | 0.3% | Active (ads are watched) |
| DOWNLOAD | 6 | 1,951 | 0.2% | **Not activity at all** |

**The keepalive families are a precise 40s timer.** `buffer-health`, `video-resize`, and
`network-bandwidth` have p25 = p50 = p75 = 40s, an IQR/median ratio of **exactly 0.0**.
`network-activity` is 32/40/40. This is a machine-generated cadence, and it firmly
contradicts the dictionary's "every 1 minute".

**No single heartbeat family can carry liveness.** Coverage, in sessions missed:

| Liveness basis | Sessions covered | Sessions missed |
|---|---|---|
| `network-activity` alone | 9,630 | 1,236 |
| `buffer-health` alone | 9,582 | 1,284 |
| `video-resize` alone | 7,791 | 3,075 |
| Union of all three | 9,708 | **1,158** |
| **Any signal at all** | **10,866** | **0** |

Even the union of all three keepalives misses 1,158 sessions (10.7%). This is why the
liveness gate must key off *any* signal in the timeline rather than a designated
heartbeat event — a design choice that follows directly from this measurement.

**Which events fire while backgrounded.** These prove the app is *running*, not *visible*:
`pause` (15.7% of its occurrences), `dropped-frames` (13.8%), `network-change` (25.3%),
`download_completed` (26.0%), plus small but non-zero rates on every keepalive family.
Any model that reads these as "watching" re-imports the background time it just removed.

**`BufferStart`/`BufferEnd` are a trap in the opposite direction.** They look like
inactivity but buffering mid-playback *is* watching. They fire at p50 2.8s intervals with
a high IQR ratio (event-driven, not periodic), and only 0.2% occur while backgrounded.
Treated as a stop signal, they would fragment nearly every session and undercount.

**Downloads are playback-independent.** `download_asset_played`, `download_initiated`, and
`download_completed` can proceed with no playback at all and while backgrounded (26% for
`download_completed`). They must not open an active interval.

**UX events as a foreground repair signal.** 45 UX events across 15 sessions fire while
the carried state says "background". A user cannot seek an invisible player, so these are
dropped `AppForegrounded` markers (the dictionary warns these are "not guaranteed"). The
volume is small enough that we do *not* use it in V3, but it is quantified so the decision
is evidence-based rather than an oversight.

---

## 2b. Event sequencing — the state machine

`out_08.txt`. 48 raw event values reduce to a 9-signal alphabet
(`START`/`PLAY`/`HB`/`PAUSE`/`RESUME`/`BG`/`FG`/`ERR`/`END`) with no loss of
state-changing information.

**Opening signatures** (first 4 signals):

| Opening | Sessions | % |
|---|---|---|
| `START → PLAY → HB → HB` | 7,916 | 72.9% |
| `START → PLAY → HB → PAUSE` | 972 | 9.0% |
| `START → PLAY → PAUSE → BG` | 468 | 4.3% |
| `START → HB → PLAY → HB` | 308 | 2.8% |
| `START → BG → FG → PLAY` | 306 | 2.8% |

`START → PLAY` covers 92.9% of transitions out of `START`. The playback-before-state
ordering is what justifies the foreground default.

**Closing signatures** (last 4): `HB → HB → HB → END` is 48.9%, `PAUSE → HB → HB → END`
17.1%, and `HB → HB → END → BG` 1.3% — that last one is a session emitting signals *after*
declaring itself over. Closing shape drives open-session policy: a session ending
`… → BG → END` spent its tail inactive, while `… → HB → END` was active to the close.

**Most common trigrams** in the state-only sequence:

| Trigram | Occurrences | % |
|---|---|---|
| `PAUSE → BG → FG` | 10,994 | 11.0% |
| `BG → FG → RESUME` | 8,840 | 8.8% |
| `START → PLAY → PAUSE` | 8,344 | 8.3% |
| `PAUSE → RESUME → PAUSE` | 8,161 | 8.2% |
| `RESUME → RESUME → RESUME` | 7,078 | 7.1% |

`PAUSE → BG → FG` being the single most common trigram is a real behavioural finding: the
client pauses *just before* backgrounding, not during. So the foreground and playing gates
are correlated **in sequence** rather than nested in time — only 11% of background windows
actually contain a pause marker. This is why their savings are not additive.

**The gates are not independent, and the report does not pretend otherwise.** Exclusion
attributed to combined reasons rather than credited to one gate:

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

Two very different failure modes sit in this table. The largest excluded block (839 h) is
only 4,814 segments — abandoned sessions left backgrounded and stale, enormous time from
few segments. The most *numerous* excluded cell (122,378 segments, 245.9 h) is ordinary
users pausing while watching. A naive model conflates them; they need different handling.

**Session archetypes.** 10,866 sessions collapse to five shapes, with retention measured
per shape:

| Shape | Sessions | % | Wall-clock h | Active h | **Retained** | Avg intervals |
|---|---|---|---|---|---|---|
| B. single excursion | 8,214 | 75.6% | 1,961.7 | 1,258.0 | **64.1%** | 2.61 |
| C. multi excursion (2+) | 2,034 | 18.7% | 748.9 | 431.8 | **57.7%** | 4.35 |
| D. ended backgrounded | 313 | 2.9% | 63.5 | 31.4 | **49.4%** | 2.76 |
| E. errored | 293 | 2.7% | 64.8 | 37.1 | **57.3%** | 3.20 |
| F. marathon (>6h) | 12 | 0.1% | 133.5 | 4.9 | **3.7%** | 2.25 |

Retention ranges 3.7% to 64.1%. **No blanket correction factor applied to naive
concurrency could be right for all five.**

Note the archetype "never backgrounded" has **zero** sessions: every session in this file
backgrounds at least once. That is a synthetic-generator fingerprint and is **not safe to
assume on the unseen day** — the code must still handle a session with no state markers.

**Contradictory transitions that must be tolerated idempotently:** `RESUME → RESUME`
(1,778), `END → BG` (213), `PAUSE → PAUSE` (189), `BG → BG` (98), `FG → FG` (35),
`END → PLAY` (15), `START → START` (14), `END → END` (12). The `END → *` family is the
dangerous one.

**Why state carry-forward, not pattern matching.** The state-only path (heartbeats
removed) has **2,655 distinct paths** across 10,866 sessions; the most common covers only
10.8%. Enumerating rules over these shapes does not generalise. Carrying state forward
does, and it handles all the contradictory transitions above for free.

---

## 3. The two delta-model bugs

This is the most consequential technical finding, and it is why script 03 asserts rather
than reports.

**Bug 1 — per-interval deltas double-count a session.** Concurrency counts *distinct
sessions*, not intervals. A session that pauses and resumes twice inside one minute has
three active intervals in that minute; emitting `+1` per interval counts it three times.
There are **4,854 spurious `+1`s** in this dataset, and 6,818 of 10,850 sessions have
intervals that collapse into fewer minute-runs.

*Fix:* reduce to distinct `(session, minute)` occupancy, merge contiguous minutes into
runs, then emit deltas per run. 32,122 intervals collapse to 16,518 runs.

**Bug 2 — the delta table is sparse, so a plain cumulative sum skips minutes.** Only
1,490 minutes carry a delta, but **3,649 minutes are actually occupied**. A running sum
over only the rows that exist attributes concurrency to the wrong minutes and reports
1,835 non-empty minutes instead of 3,649.

*Fix:* the serving query must densify the minute axis over the requested window and
carry the running sum across minutes with no delta row.

With both fixed, the model is exact — asserted, not eyeballed:

```
ASSERTION global:    minute-run delta + dense fill == brute force -> PASS  (0 mismatching minutes)
ASSERTION per-slice: 0 mismatching group-minutes                  -> PASS  (per platform × content_id)
```

Peak 2,697 and average 34.8476 match brute-force interval overlap exactly, globally and
per dimension combination.

---

## 4. Peak and average semantics

Three traps, all measured.

**Peak is not additive.** Summing per-platform peaks gives 2,786 against a true overall
peak of 2,697 — an 89-session overstatement. Every slice peaks at its **own minute**:
`ANDROID_PHONE` at 10:56, `IPHONE` at 10:55, `SONY_ANDROID_TV` at 10:53, `Mweb` at
11:02. By `video_type`, `vod` peaks at 11:02 and `live` at 10:42, twenty minutes apart.
This confirms the scenario in the problem statement: peak must be computed at the
requested filter combination, never assembled from parts.

**Peak does not roll up from a coarser grain, but is invariant to it.** Peak over a
window is always the max minute in that window, so it reads 2,697 at minute, hour, and
day grain. What breaks is computing it *from* pre-aggregated coarse values: a max of
hourly averages, or an average of hourly peaks, are different and wrong numbers. Store
minute grain; roll up with `max()`.

**Average has three defensible answers, and they differ by 4x.** For the same question:

| Interpretation | Value |
|---|---|
| Mean over occupied minutes | 34.85 |
| Mean over every minute in span (incl. 13,379 empty) | 7.47 |
| Time-weighted (active-minutes ÷ occupied minutes) | 28.99 |

The divergence is driven by partial-minute occupancy: **50.1% of active intervals are
shorter than one minute** and 6,570 are under 5 seconds. Minute-count averages credit a
full minute; the time-weighted form credits the actual fraction. A benchmark answer must
state which definition it uses — this is the single most likely source of a mismatch
against a private answer key that is otherwise correct.

**Session ≠ user concurrency.** Peak session concurrency is 2,697 against peak user
concurrency of 2,629, with up to 82 sessions-over-users in a single minute (17,397
overlapping same-user session pairs; one user has 301 sessions). More importantly,
`countDistinct(user)` is **not delta-summable** — a user on two devices must count once —
so a user-grain serving table needs a mergeable sketch
(`AggregateFunction(uniqCombined, ...)`), not a counter.

---

## 5. Representation and serving layout

Row counts for the same information (`out_03.txt`, `out_06.txt`):

| Representation | Rows | Note |
|---|---|---|
| Raw input events | 905,558 | |
| Per-minute explosion (session × minute) | 127,159 | grows with watch duration |
| Active intervals (session × interval) | 32,122 | |
| Merged minute-runs (session × run) | 16,518 | delta-safe unit |
| Minute deltas × (platform, content) | 22,676 | |
| Minute deltas, global | 1,490 | |

The per-minute explosion is 5.6x the delta table here, and **that multiplier grows with
watch duration** — a longer session adds one row per minute but still only two deltas.
That is the structural argument against per-minute materialisation, independent of
current volume.

**Serving-table width is the real design tension.** Delta rows by dimension set:

| Dimension set | Combos | Delta rows |
|---|---|---|
| global / country | 1 | 1,490 |
| platform (+country) | 10 | 2,143 |
| platform, country, video_type | 29 | 2,871 |
| content_id | 3,357 | 19,294 |
| platform, country, content_id, video_type | 4,317 | 22,676 |
| + app_version, audio_language | 5,025 | 24,479 |

Adding `content_id` inflates the table 9x, and `content_id` is simultaneously the
highest-cardinality dimension and one of the most-requested filters. A single wide
pre-aggregate does not stay small — this argues for tiering: a narrow
`(platform, country, video_type)` table for dashboard defaults, plus a
content-inclusive table for content drill-down.

The cube is extremely sparse: 3,649 occupied minutes × 4,317 combos = 15.7M dense cells
against **22,676 actual rows (0.14% density)**, because an asset is only watched in a
narrow window. Store sparse deltas ordered by `(dims…, minute)`; never a dense grid.
Average rows per key prefix is 5.26, so a filtered query seeks the prefix and scans a
short contiguous run — this is what makes ClickHouse's sparse primary index the right
fit. 740 rows have deltas netting to zero and can be dropped at merge.

Load is skewed enough to matter for ordering-key choice: `ANDROID_PHONE` is 66.7% of
session-minutes, and the top content is 10.7% (top 10 = 25.8%, top 100 = 47.3%).

---

## 6. Update handling and open sessions

**This dataset closes every session.** All 10,866 have both a start and an end, so
`sessions_without_end = 0`. The problem statement promises sessions still open when the
day ends, and the unseen day is unlikely to be this tidy. **The open-session path must be
built and tested even though this file never exercises it** — we simulate it by
truncating at a watermark.

At a watermark 25 minutes before the last event, 3,338 sessions are open. Three policies:

| Policy | Concurrency at watermark minute | Peak |
|---|---|---|
| P1 drop open sessions | 4 | 1,437 |
| **P2 active until last signal** | **430** | **2,695** |
| P3 assume active to watermark | 2,087 | 2,697 |

P1 erases the live edge (the number a live-event dashboard exists to show). P3 assumes
anyone who went quiet is still watching. P2 plus the liveness gate is the honest
position: active until proven stale. Worth noting all 3,338 open sessions *did* signal
again, so P3's optimism happens to be right here — but it is right by luck, and it is
the policy that a genuine crash-and-abandon would break.

**Incrementality is the strongest argument for the delta representation.** Of 772,795
consecutive heartbeat steps, **650,388 (84%) land in the same minute as the previous
one** and therefore change *no serving row at all*. Only 116,030 advance exactly one
minute. Heartbeat volume does not translate into serving-layer write volume, which is
what makes an append-only `AggregatingMergeTree` viable. The alternative — recomputing a
session's minutes on every heartbeat — would touch ~12.9M rows across the dataset
(avg 16.4 minutes per session × 783,648 heartbeats), against a worst case of 2,618
minutes for one session.

**Finalisation cannot use a fixed delay.** A minute is final only when every overlapping
session has closed. Session duration is p50 11.9 min, p95 41.5, p99 74.1, p99.9 374.6,
**max 2,618 min**. A "finalise after N minutes" rule is wrong for that tail. Recommended:
watermark-finalise the bulk, keep a small open-session overlay for the long tail.

**Lateness is not measurable in this file, and that is a finding.** There is no
ingestion/received timestamp column, and the CSV is grouped by session (96.6% of sessions
are stored contiguously), not ordered by time — 7.9% of adjacent rows go backwards in
time purely from that grouping. So any row-order-derived "lateness" figure would be an
artifact. Two consequences: build nothing that depends on input file order, and have the
pipeline stamp its own arrival time (`DEFAULT now()`) so lag is observable on the unseen
day. That ingestion-lag metric is also the natural ClickStack integration.

Separately, **11.3% of intra-session steps are out of order** (max inversion 155,604s),
so the interval builder must `ORDER BY` timestamp explicitly and never trust insertion
order for state carry-forward.

---

## 7. Edge case census

Full detail and a recommended policy per case in `out_04.txt`. The ones that change
results:

| Case | Count | Policy |
|---|---|---|
| Exact duplicate event rows | 3,413 groups / 4,210 excess rows | Dedupe on (session, type, event, ts). Deltas are additive, so a duplicated boundary event **permanently** skews concurrency. |
| Signals inside background windows | 3,674 across 2,361 sessions | Foreground state wins over heartbeat presence. |
| Events after `VideoSessionEnd` | 802 across 239 sessions (max 34.7 min late) | Clamp to session end; trailing beats are flush-on-exit retries. |
| Unbalanced BG/FG | 418 more-BG, 48 more-FG | Carry state forward, clamp at end. Never read missing FG as foreground. |
| Last state event is BG | 344 sessions, 6.7 h | Exclude the tail. |
| Duplicate lifecycle events | 13 start / 14 end / 16 play | `min(start)`, `max(end)`; at-least-once delivery, not new sessions. |
| Same-ms signal collisions | 161,660 (6,058 with differing kinds) | Deterministic tie-break priority, or runs are non-reproducible. |
| Sessions > 6h / > 24h | 12 / 1 (max 43.6h) | Liveness gate bounds them. |
| Dimension drift mid-session | 95 platform, 120 user, 6,864 audio-lang | Pin dims at session start, else one session lands in two slices. |
| Blank/sentinel dimensions | 802,984 rows `subtitle_language` in {UNK, OFF}; 51,185 audio `unk` | Normalise to one `unknown` token at ingest. |
| Blank `video_type` in metadata | 1,089 content rows → 250 sessions | **LEFT JOIN + coalesce, never INNER** — an inner join silently deletes these from every `video_type` answer. |
| Sessions crossing UTC day | 11 (3,882 cross an hour) | Count in every minute spanned; day-partitioned tables need cross-partition reads. |
| `VideoError` sessions | 293 (238 → immediate END, 55 continue) | Do **not** treat ERR as a stop signal; 55 recover and keep playing. |
| Sessions with zero active time | 16 | Must not be counted at all. |

Cases that are clean here but should still be defended for the unseen day: no nulls in
any key column, no negative durations, `session_start_epoch` agrees with the
`VideoSessionStart` event in all 10,866 sessions, and content metadata covers 100% of
referenced `content_id`s.

That last one is a useful bonus: because `session_start_epoch` is on **every** row and
always correct, it can serve as a partition/sort key without first locating a session's
start row.

---

## 8. Recommended design

Follows from the findings above.

**Ingest.** Raw events land in a `MergeTree` ordered by
`(video_session_id, event_timestamp)` with an ingestion timestamp defaulted to `now()`.
Normalise sentinel dimension values here. Dedupe on
`(session, event_type, event, event_timestamp)`.

**Session state layer.** Per-session active minute-runs, maintained incrementally.
Keyed by session; dimensions pinned at session start. Open sessions live here and mutate;
closed sessions are finalised past the watermark.

**Serving layer.** `AggregatingMergeTree` of per-minute deltas, ordered
`(dims…, minute_bucket)`. Two widths: narrow `(platform, country, video_type)` for
dashboard defaults, content-inclusive for drill-down. Queries seek the key prefix,
densify the minute axis over the requested window, cumulative-sum, then `max()` or
`avg()` at the requested grain.

**Query contract.** Peak = `max()` over minute concurrency inside the window at the
requested filter combination. Average = must be declared; recommend mean over occupied
minutes, with the time-weighted variant available, given the 4x spread.

**Integration.** ClickStack on the pipeline's own ingestion lag and query latency — the
natural fit precisely because lateness is unobservable in the input and must be measured
by the pipeline itself.

---

## 9. Files

| File | Contents |
|---|---|
| `analysis/common.py` | DuckDB loading, formatting helpers |
| `analysis/intervals.py` | Signal normalisation and the parameterised active-interval builder (all four gate variants) |
| `analysis/01_profile_raw.py` | Volume, event mix, cardinality, nulls, timestamp sanity, content join |
| `analysis/02_active_interval_semantics.py` | Gate-by-gate impact, timeout sweep, per-dimension divergence |
| `analysis/03_concurrency_model_validation.py` | Delta model vs brute force (asserted), peak/average semantics, storage comparison |
| `analysis/04_edge_cases.py` | 18-case census, each with a recommended policy |
| `analysis/05_update_and_openness.py` | Open-session policies, incrementality, finalisation, ordering |
| `analysis/06_query_surface_and_scale.py` | Filter space, serving cardinality by dimension set, scale ratios |
| `analysis/07_heartbeat_taxonomy.py` | Empirical classification of all 41 heartbeat sub-events; periodicity, background/pause rates, liveness coverage |
| `analysis/08_sequence_patterns.py` | Transition matrix, opening/closing signatures, trigrams, archetypes, gate decision table, default-state justification |
| `analysis/run_all.sh` | Runs everything; `--fresh` reloads from CSV |

DuckDB is the analysis engine here, chosen because its SQL semantics are close enough to
ClickHouse that this logic ports with minimal rewriting. **It is not a substitute for the
ClickHouse implementation the problem requires** — it is the correctness reference to
validate that implementation against.

### Caveats

- Peak/average figures are for the V3 definition. A different active-interval definition
  yields different numbers; §2 quantifies the spread.
- `country` has one value and `subtitle_language` two effective values in this file.
  Conclusions about their cardinality do not transfer to the unseen day.
- The 100x figures are ratio-based extrapolation, not a benchmark. Dimension combos grow
  with volume, so delta rows will not scale exactly linearly.
- No timing measurements here. Query latency must be measured on ClickHouse Cloud at the
  real data volume; DuckDB timings on a local file would not be meaningful.
