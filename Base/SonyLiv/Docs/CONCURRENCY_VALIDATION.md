# Concurrency Validation — Actual vs Computed

Validates the v2 pipeline's concurrency output (`cc_delta_content`, per
`src/migrationv2/migrations/`) against an independent ground truth, on the
live `rohitdevtesting` ClickHouse database.

- **Actual (ground truth)**: computed in Python, session-by-session, directly
  from raw events (`ch_hackathon_raw_data` + `ch_hackathon_content_data`),
  independent of the SQL pipeline. Built by
  `Base/SonyLiv/evals/reference_intervals.py` (state machine: fg/playing/ended,
  90s silence timeout) → `reference_intervals.csv`.
- **Computed (pipeline)**: queried live from `cc_delta_content`, the
  deployed serving table.

Both pulled 2026-08-02 against `rohitdevtesting`.

## Methodology — the computed-value query

Per-minute concurrency, correctly seeded from the start of the day (not the
start of the query window — see **Finding 1** below for why that matters):

```sql
WITH stepped AS (
    SELECT minute, sum(d) OVER (ORDER BY minute) AS concurrent_viewers
    FROM (
        SELECT minute, sum(delta_sessions) AS d
        FROM cc_delta_content
        WHERE toDate(minute) = '2026-07-26'
        GROUP BY minute
        ORDER BY minute WITH FILL
            FROM toDateTime('2026-07-26 00:00:00')
            TO   toDateTime('2026-07-27 00:00:00')
            STEP INTERVAL 1 MINUTE
    )
)
SELECT minute, concurrent_viewers FROM stepped
WHERE minute >= toDateTime('2026-07-26 10:00:00') AND minute < toDateTime('2026-07-26 11:00:00')
ORDER BY minute;
```

Range-level peak/avg (used for the summary table) is the same running-sum
idea, clipped and time-weighted over an arbitrary `[start, end)` — see
`peak_avg_from_delta_dims` in `Base/SonyLiv/evals/test_range_queries.py`.

### Finding 1 — the naive windowed query undercounts (bug in the query shape you gave us)

The query as originally written puts the day-range filter (`WHERE minute >=
'10:00:00' AND minute < '11:00:00'`) *before* the running-sum window
function, so `sum(d) OVER (ORDER BY minute)` only sums deltas that fall
inside 10:00–11:00 — it discards whatever concurrency was already carried in
from before 10:00:

| query shape | concurrent_viewers at 10:00:00 |
|---|---:|
| filter-then-sum (naive) | 4 |
| sum-then-filter, seeded from day start (correct) | 54 |

The correct value at 10:00:00 is 54, not 4 — 9-14% of that hour's activity
had already started before 10:00. Always seed the running sum from the start
of the day (or from a checkpoint known to be 0), then filter the *output*
range, never filter before the window function.

## Range / filter summary — actual vs computed

Peak and time-weighted average concurrency for 7 time ranges × 4 dimension
filters (28 combos). Tolerance: ±3% (rel) or ±3 sessions (floor), whichever
is looser.

| range | filter | peak actual | peak computed | diff% | avg actual | avg computed | diff% | distinct sessions (actual only) | distinct users (actual only) | status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| quiet_pre_event | no_filter | 39 | 43 | +10.3% | 30.10 | 38.10 | +26.6% | 82 | 73 | FAIL |
| quiet_pre_event | platform_android_phone | 36 | 39 | +8.3% | 27.93 | 35.77 | +28.1% | 74 | 67 | FAIL |
| quiet_pre_event | platform_iphone | 3 | 3 | +0.0% | 1.43 | 1.33 | -7.0% | 5 | 3 | PASS |
| quiet_pre_event | video_type_live | 0 | 0 | n/a | 0.00 | 0.00 | n/a | 0 | 0 | PASS |
| ramp_up | no_filter | 1484 | 1273 | -14.2% | 826.59 | 762.60 | -7.7% | 2452 | 2335 | FAIL |
| ramp_up | platform_android_phone | 927 | 785 | -15.3% | 529.57 | 478.50 | -9.6% | 1480 | 1450 | FAIL |
| ramp_up | platform_iphone | 204 | 179 | -12.3% | 107.53 | 107.20 | -0.3% | 350 | 335 | FAIL |
| ramp_up | video_type_live | 310 | 246 | -20.6% | 169.26 | 146.90 | -13.2% | 489 | 479 | FAIL |
| sustained_peak | no_filter | 2298 | 2232 | -2.9% | 2168.69 | 2126.35 | -2.0% | 6516 | 6026 | PASS |
| sustained_peak | platform_android_phone | 1457 | 1368 | -6.1% | 1366.92 | 1301.30 | -4.8% | 3919 | 3744 | FAIL |
| sustained_peak | platform_iphone | 263 | 290 | +10.3% | 237.32 | 273.70 | +15.3% | 892 | 793 | FAIL |
| sustained_peak | video_type_live | 332 | 365 | +9.9% | 264.42 | 328.20 | +24.1% | 1432 | 1362 | FAIL |
| single_peak_minute | no_filter | 2297 | 2203 | -4.1% | 2280.51 | 2203.00 | -3.4% | 2676 | 2604 | FAIL |
| single_peak_minute | platform_android_phone | 1457 | 1350 | -7.3% | 1445.93 | 1350.00 | -6.6% | 1681 | 1672 | FAIL |
| single_peak_minute | platform_iphone | 263 | 289 | +9.9% | 253.15 | 289.00 | +14.2% | 331 | 328 | FAIL |
| single_peak_minute | video_type_live | 322 | 344 | +6.8% | 315.06 | 344.00 | +9.2% | 389 | 388 | FAIL |
| ramp_down | no_filter | 1910 | 1852 | -3.0% | 1087.22 | 1086.40 | -0.1% | 4426 | 4166 | FAIL |
| ramp_down | platform_android_phone | 1198 | 1129 | -5.8% | 685.78 | 660.90 | -3.6% | 2696 | 2630 | FAIL |
| ramp_down | platform_iphone | 211 | 216 | +2.4% | 119.95 | 126.60 | +5.5% | 592 | 549 | FAIL |
| ramp_down | video_type_live | 205 | 287 | **+40.0%** | 132.83 | 194.70 | **+46.6%** | 1069 | 1021 | FAIL |
| full_event_hour | no_filter | 2298 | 2232 | -2.9% | 854.41 | 809.37 | -5.3% | 6754 | 6236 | FAIL |
| full_event_hour | platform_android_phone | 1457 | 1368 | -6.1% | 551.87 | 506.65 | -8.2% | 3994 | 3802 | FAIL |
| full_event_hour | platform_iphone | 263 | 290 | +10.3% | 97.28 | 108.02 | +11.0% | 958 | 855 | FAIL |
| full_event_hour | video_type_live | 356 | 365 | +2.5% | 131.50 | 132.70 | +0.9% | 1417 | 1355 | PASS |
| full_event_day | no_filter | 2298 | 2232 | -2.9% | 69.58 | 65.54 | -5.8% | 10502 | 9331 | FAIL |
| full_event_day | platform_android_phone | 1457 | 1368 | -6.1% | 45.70 | 40.91 | -10.5% | 6270 | 5745 | FAIL |
| full_event_day | platform_iphone | 263 | 290 | +10.3% | 7.51 | 7.86 | +4.7% | 1485 | 1249 | FAIL |
| full_event_day | video_type_live | 356 | 365 | +2.5% | 8.73 | 10.31 | +18.1% | 2643 | 2455 | PASS |

**5/28 pass.** `distinct sessions`/`distinct users` are actual-only reference
values — v2 dropped `cc_si_minute` (the HLL sketch table), so the pipeline
has no identity-preserving serving table and cannot answer "how many
distinct sessions/users" at all today. Shown for sizing, not compared.

## Minute-by-minute — sustained peak hour (2026-07-26 10:00–11:00)

Actual = ground truth from `reference_intervals.csv` (count of active
intervals covering that minute mark). Computed = the corrected query above
against `cc_delta_content`.

| minute | actual | computed | diff | diff% |
|---|---:|---:|---:|---:|
| 10:00:00 | 46 | 54 | +8 | +17.4% |
| 10:01:00 | 49 | 52 | +3 | +6.1% |
| 10:02:00 | 46 | 53 | +7 | +15.2% |
| 10:03:00 | 48 | 52 | +4 | +8.3% |
| 10:04:00 | 51 | 53 | +2 | +3.9% |
| 10:05:00 | 56 | 53 | -3 | -5.4% |
| 10:06:00 | 45 | 55 | +10 | +22.2% |
| 10:07:00 | 52 | 57 | +5 | +9.6% |
| 10:08:00 | 58 | 58 | +0 | +0.0% |
| 10:09:00 | 65 | 59 | -6 | -9.2% |
| 10:10:00 | 64 | 59 | -5 | -7.8% |
| 10:11:00 | 68 | 62 | -6 | -8.8% |
| 10:12:00 | 65 | 66 | +1 | +1.5% |
| 10:13:00 | 72 | 65 | -7 | -9.7% |
| 10:14:00 | 65 | 66 | +1 | +1.5% |
| 10:15:00 | 74 | 69 | -5 | -6.8% |
| 10:16:00 | 77 | 74 | -3 | -3.9% |
| 10:17:00 | 81 | 74 | -7 | -8.6% |
| 10:18:00 | 84 | 76 | -8 | -9.5% |
| 10:19:00 | 86 | 73 | -13 | -15.1% |
| 10:20:00 | 82 | 74 | -8 | -9.8% |
| 10:21:00 | 86 | 74 | -12 | -14.0% |
| 10:22:00 | 87 | 77 | -10 | -11.5% |
| 10:23:00 | 94 | 76 | -18 | -19.1% |
| 10:24:00 | 92 | 79 | -13 | -14.1% |
| 10:25:00 | 87 | 77 | -10 | -11.5% |
| 10:26:00 | 85 | 75 | -10 | -11.8% |
| 10:27:00 | 84 | 75 | -9 | -10.7% |
| 10:28:00 | 82 | 77 | -5 | -6.1% |
| 10:29:00 | 81 | 78 | -3 | -3.7% |
| 10:30:00 | 75 | 199 | **+124** | **+165.3%** |
| 10:31:00 | 243 | 346 | +103 | +42.4% |
| 10:32:00 | 420 | 466 | +46 | +11.0% |
| 10:33:00 | 571 | 597 | +26 | +4.6% |
| 10:34:00 | 725 | 716 | -9 | -1.2% |
| 10:35:00 | 843 | 847 | +4 | +0.5% |
| 10:36:00 | 985 | 971 | -14 | -1.4% |
| 10:37:00 | 1139 | 1049 | -90 | -7.9% |
| 10:38:00 | 1208 | 1162 | -46 | -3.8% |
| 10:39:00 | 1338 | 1273 | -65 | -4.9% |
| 10:40:00 | 1480 | 1367 | -113 | -7.6% |
| 10:41:00 | 1581 | 1475 | -106 | -6.7% |
| 10:42:00 | 1698 | 1552 | -146 | -8.6% |
| 10:43:00 | 1747 | 1651 | -96 | -5.5% |
| 10:44:00 | 1838 | 1715 | -123 | -6.7% |
| 10:45:00 | 1863 | 1769 | -94 | -5.0% |
| 10:46:00 | 1938 | 1847 | -91 | -4.7% |
| 10:47:00 | 1964 | 1904 | -60 | -3.1% |
| 10:48:00 | 2067 | 1944 | -123 | -6.0% |
| 10:49:00 | 2084 | 1992 | -92 | -4.4% |
| 10:50:00 | 2125 | 2043 | -82 | -3.9% |
| 10:51:00 | 2143 | 2079 | -64 | -3.0% |
| 10:52:00 | 2181 | 2127 | -54 | -2.5% |
| 10:53:00 | 2202 | 2180 | -22 | -1.0% |
| 10:54:00 | 2243 | 2181 | -62 | -2.8% |
| 10:55:00 | 2270 | 2203 | -67 | -3.0% |
| 10:56:00 | 2283 | 2225 | -58 | -2.5% |
| 10:57:00 | 2274 | 2230 | -44 | -1.9% |
| 10:58:00 | 2229 | 2228 | -1 | -0.0% |
| 10:59:00 | 2220 | 2232 | +12 | +0.5% |

### Finding 2 — sharp spike right at 10:30 (+165%)

`10:30:00` is exactly the `ramp_up` range's defined start boundary
(`Base/SonyLiv/evals/golden_ranges.py`). Computed jumps from 78 (10:29) to
199 (10:30) — a 121-session single-minute jump — while actual only moves
78→75. Looks like a batch of activations that should be spread earlier (or
smoothed across a few minutes) all land in the 10:30 bucket in the computed
series. Worth checking whether this is a genuine burst in the raw data at
that exact minute, or an artifact of how the fold buckets `toStartOfMinute`
on batches that span a fold-refresh boundary (`delta_fold_mv` runs every 30s,
`checkpoint_advance_mv` every 35s — a batch that straddles two refresh
cycles could get its `min(activation_ts)` pulled toward one bucket).

### Finding 3 — two distinct divergence patterns (not one bug)

- **Under-counts during ramp-up** (10:09–10:29, steady -5% to -19%): v2's
  activation trigger (`008_delta_fold.sql`, `activation_ts` CTE) only fires
  on `event_type = 'VideoPlay'` OR (`VideoHeartbeat` AND `event IN ('play',
  'resume')`). If a session's real first watch signal doesn't hit one of
  those exact conditions, activation is missed or delayed — shows up most
  during ramp-up when many sessions are activating for the first time.
- **Over-counts once traffic is high / declining** (10:34 onward mostly
  within tolerance, but `ramp_down`/`platform_iphone`/`video_type_live`
  combos hit +40-47%): v2 only deactivates on `VideoSessionEnd` or 90s of
  total silence — any other heartbeat type keeps a session "active" through
  a `pause`, unlike the ground-truth state machine which drops a session
  the instant it pauses. Sessions v2 keeps alive that the reference already
  ended pile up during decline.

Both are **spec questions**, not obviously bugs: which activation/
deactivation rule is v2 supposed to implement? The ground-truth reference
still encodes the older fg/playing/ended model
(`Base/SonyLiv/evals/reference_concurrency.py`); v2's fold implements a
simpler activate-until-ended-or-stale model. Recommend the team confirm
which is the intended v2 definition — if v2's simpler model is intentional,
the reference/golden fixtures need updating to match, not the other way
around.

## Reproduction

```bash
cd Base/SonyLiv/evals
python3 reference_intervals.py      # ground truth intervals -> reference_intervals.csv
python3 golden_ranges.py            # range/filter golden values -> golden_ranges.json
python3 test_range_queries.py       # runs the summary-table checks live
```
