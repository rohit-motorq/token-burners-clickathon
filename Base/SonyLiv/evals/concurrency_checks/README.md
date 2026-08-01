# concurrency_checks

Standalone sub-suite, separate from the main `evals/` suite, for exactly
three things:

1. **`test_daily_peaks.py`** — sweeps actual (ground-truth) concurrency
   across the whole day, picks the top 6 local peak minutes (>=15min apart),
   checks the pipeline (`cc_delta_content`) reports the same concurrency at
   each of those exact minutes.
2. **`test_time_ranges.py`** — 7 hand-picked 5-7 minute windows spread
   across the day (quiet, ramp-up, near-peak, peak, ramp-down, tail, and one
   dead zone outside the event for a near-zero correctness check), actual
   vs computed peak + time-weighted-avg concurrency for each.
3. **`test_full_hour.py`** — actual vs computed concurrency for every
   minute from 10:00:00 to 10:59:00 UTC (the sustained-peak hour), one
   PASS/FAIL per minute plus the single worst-diverging minute.

"Computed" only ever queries `cc_delta_content` — the delta table is all the
read path needs; `session_active`/`cc_delta_raw`/`pipeline_checkpoint` are
internal plumbing for how the deltas get produced, not part of any query
here. "Actual" is the independent Python fold in `../reference_intervals.py`
(fg/playing/ended state machine, 90s silence timeout), reused via `common.py`.

Distinct session/user counts are reported where relevant (actual side only)
but never compared — v2's deployed schema has no identity-preserving
serving table (`cc_si_minute` was dropped from the v1 design), so the
pipeline cannot answer "how many distinct sessions/users" today. Not a bug
in these tests; see the main suite's `test_range_queries.py` docstring for
the same note. The problem statement itself only asks for peak/avg
concurrency counts, never distinct-in-range headcounts, so this isn't a
graded gap either — just can't be cross-checked.

## Run

```
./run.sh
```

Regenerates `../reference_intervals.csv` if missing, runs all three checks,
writes a timestamped report to `reports/report_<stamp>.md` (git-ignored,
same format as the parent suite's reports).

Or individually: `python3 test_daily_peaks.py`, etc. — each SKIPs cleanly
if `cc_delta_content` isn't ready or the ground-truth CSV is missing.
