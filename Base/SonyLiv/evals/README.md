# Evals

Two layers, run in order by `./run.sh`:

1. **`data_expectations.py`** — codifies the claims in `Docs/DATA_ANALYSIS-ROHIT.md`
   as live SQL assertions against `ch_hackathon_raw_data` / `ch_hackathon_content_data`.
   This is the contract: schema, event types, 1:1 session-content, heartbeat gap
   distribution, 90s-timeout coverage, terminal-error absorbance, dedup-relevant
   duplicate transitions. Run any time, independent of the pipeline.

2. **`reference_concurrency.py`** — an independent ground truth. Folds raw events
   session-by-session in plain Python (not SQL, not reused from the pipeline)
   using the exact state machine from HLD-sam.html §06 / DataFlow-Sam.html §2.5:
   `fg`/`playing`/`ended` switches, 90s silence timeout, absorbing terminal state.
   Writes per-minute per-dimension delta rows to `reference_deltas.csv`.
   Sanity-checked against the EDA's own reported numbers: peak minute matches
   exactly (10:55 UTC on 2026-07-26), peak magnitude within ~1.4%.

3. **`test_benchmarks.py`** — runs the benchmark query set from HLD/DataFlow §7-8
   against whatever serving tables exist (`cc_delta_dims`, `cc_delta_content`,
   `cc_si_minute`, `session_state`), and checks:
   - SA curve matches the reference (2% tolerance) for a filtered dimension
   - concurrency never negative
   - SA ≤ SI invariant
   - peak minute genuinely shifts across dimension filters (catches an
     over-flattened serving table)
   - the hour-boundary reset trick is exact (no silent history dependency)
   - `session_state` "right now" count is same order of magnitude as the
     latest batched delta total
   - per-content drill-down is sane

   **Every test SKIPs (not fails) if its required table doesn't exist yet** —
   safe to run before the pipeline is built; re-run after with no changes.

4. **`reference_intervals.py`** — same fold as (2), but emits the flat active-
   interval list (session_id, user_id, dims, start_ts, end_ts) instead of
   pre-aggregated minute deltas. Feeds `range_metrics.py`, a pure-Python
   range-query engine (peak / time-weighted-avg / distinct-session-count /
   distinct-user-count for any `[start, end)` window + dimension filter).

5. **`golden_ranges.py`** — picks concrete time ranges out of the real event
   day (quiet pre-event, ramp-up, sustained peak, one peak minute, ramp-down,
   the full event hour, the full day) crossed with a few dimension filters,
   and computes their golden peak/avg/distinct-session/distinct-user values
   via `range_metrics.py`. Writes `golden_ranges.json` — 28 range×filter
   fixtures with concrete expected numbers, e.g. `sustained_peak` (10:47–
   11:07 UTC) golden peak=2298, avg=2168.69, distinct_sessions=6516,
   distinct_users=6026.

6. **`test_range_queries.py`** — runs each golden fixture through the
   serving tables:
   - peak/avg concurrency: answered **exactly** by `cc_delta_dims` (running
     sum of deltas restricted to the window) — tight tolerance vs golden.
   - distinct active sessions/users: **not exactly answerable** by the
     current design — `cc_delta_dims`/`cc_delta_content` intentionally drop
     session/user identity to stay cheap at scale. The only identity-
     preserving sketches are in `cc_si_minute`, which counts naive presence
     (backgrounded/paused time included, ~9% over per the EDA). So this is
     checked as an **upper bound**: golden foreground-only distinct count
     must be `<=` `cc_si_minute`'s merged uniqCombined sketch for that
     range. If you need an exact distinct-user-in-range number, that's a
     design gap to raise with the team, not something this test can silently
     paper over.

7. **`test_schema.py`** — checks every serving table's columns against
   LLD-sam.md §2 DDL exactly (`session_state`, `cc_delta_content`,
   `cc_delta_dims`, `cc_si_minute`, `session_runs`, `pipeline_cursor`,
   `content_dim`, `events_raw`). Catches "table exists but wrong shape"
   before query tests hit a confusing SQL error. SKIP per table if it
   doesn't exist; FAIL if it exists missing an LLD-specified column.

8. **`test_ledger.py`** — `session_runs` (LLD §2.7), the only serving table
   that keeps both session identity and active-interval semantics, so it's
   the one place a per-session cross-check against `reference_intervals.csv`
   is possible (aggregate delta tables discard identity by design). Checks:
   `sign` only ever ±1, `run_start < run_end`, no two active runs overlap
   for the same session, and per-session active-run COUNT matches the
   reference exactly for a 200-session sample.

9. **`test_edge_cases.py`** — operationalizes `Docs/EDGE_CASES.md`'s 25
   edge cases + the #0 critical bug (intra-minute flapping) as live checks:
   naive-delta vs correct peak (negative control), duplicate-transition
   dedup, terminal-state absorption of late events, `AppForegrounded`-alone
   non-activation, sparse-delta gap-minute correctness, SI phantom-audience
   evidence, `VideoError` non-terminality, dimension pinning under drift,
   timeout-closes-mismatched-BG/FG, day-boundary partitioning, marathon-
   session capping, zero-duration-session no-runs. File header has the full
   coverage matrix — which edge case maps to which test, which are covered
   by other files in this suite, and which are informational-only / N/A to
   the design and intentionally untested.

`table_ready()` (not just `table_exists()`) gates every query test — a table
can be DDL'd before the pipeline starts ingesting, and an empty table isn't
a correctness failure, so those report SKIP, not FAIL.

**Known gap, not covered by any test here:** retraction (LLD §10) — a late
event landing inside already-emitted history triggers a re-fold of just that
session. This is a *pipeline mutation over time* (state before vs. after a
retraction event arrives), not a static value comparison, so it can't be
checked by comparing one snapshot to the golden reference. Testing it needs
either controlling raw-event insertion order live against a running pipeline,
or reading `session_runs`' sign=-1 rows after a known retraction and
confirming the net effect nets to zero before the re-emit. Flagging for the
team rather than building a fragile integration test here.

## Run

```
./run.sh
```

Or individually:

```
python3 data_expectations.py
python3 reference_concurrency.py reference_deltas.csv
python3 test_benchmarks.py
```

## Config

`ch_client.py` reads `CH_URL` / `CH_USER` / `CH_PASS` env vars, falling back to
the hackathon-provided defaults. Override for a different ClickHouse service
(e.g. the unseen-day run):

```
CH_URL=... CH_USER=... CH_PASS=... ./run.sh
```
