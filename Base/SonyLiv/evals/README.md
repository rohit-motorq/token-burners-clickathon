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

3. **`test_schema.py`** / **`test_ordering_key.py`** — checks every serving
   table's columns, engine, and ORDER BY against `src/migrationv2/migrations/*.sql`
   exactly (`session_active`, `cc_delta_content`, `pipeline_checkpoint`,
   `content_dim`, `events_raw`). Catches "table exists but wrong shape/index"
   before query tests hit a confusing SQL error or a silently-slow query.
   SKIP per table if it doesn't exist; FAIL if missing a migration-specified
   column or the ORDER BY doesn't match (order matters — it's the index).

4. **`test_benchmarks.py`** — runs the benchmark query set from HLD/DataFlow §7-8
   against `cc_delta_content` and `session_active` (the deployed v2 schema,
   `src/migrationv2/migrations/`), and checks:
   - SA curve matches the reference (2% tolerance) for a filtered dimension
   - concurrency never negative
   - SA ≤ SI invariant — **permanent SKIP**, `cc_si_minute` dropped in v2
   - peak minute genuinely shifts across dimension filters (catches an
     over-flattened serving table)
   - the hour-boundary reset trick is exact (no silent history dependency)
   - `session_active` "right now" count (`is_active=1`) is same order of
     magnitude as the latest batched delta total
   - per-content drill-down is sane

   **Every test SKIPs (not fails) if its required table doesn't exist yet** —
   safe to run before the pipeline is built; re-run after with no changes.

5. **`reference_intervals.py`** — same fold as (2), but emits the flat active-
   interval list (session_id, user_id, dims, start_ts, end_ts) instead of
   pre-aggregated minute deltas. Feeds `range_metrics.py`, a pure-Python
   range-query engine (peak / time-weighted-avg / distinct-session-count /
   distinct-user-count for any `[start, end)` window + dimension filter), and
   `test_minute_series.py` below.

6. **`golden_ranges.py`** — picks concrete time ranges out of the real event
   day (quiet pre-event, ramp-up, sustained peak, one peak minute, ramp-down,
   the full event hour, the full day) crossed with a few dimension filters,
   and computes their golden peak/avg/distinct-session/distinct-user values
   via `range_metrics.py`. Writes `golden_ranges.json` — 28 range×filter
   fixtures with concrete expected numbers, e.g. `sustained_peak` (10:47–
   11:07 UTC) golden peak=2298, avg=2168.69, distinct_sessions=6516,
   distinct_users=6026.

7. **`test_range_queries.py`** — runs each golden fixture against
   `cc_delta_content` (peak/avg concurrency, running sum of deltas summed
   without grouping on `content_id` — same result a dedicated dims-only
   rollup would have given, see `Docs/CONCURRENCY_VALIDATION.md`). Tight
   tolerance vs golden. Distinct active sessions/users checks are a
   **permanent SKIP**: v2's deployed schema (`src/migrationv2/migrations/`)
   has no identity-preserving sketch table (`cc_si_minute` was dropped in
   the v1→v2 consolidation) — there is no way to answer "how many distinct
   sessions/users" from the serving layer today. Design gap, not a bug in
   the test; raise with the team if exact distinct-count support is needed.

8. **`test_grain_rollups.py`** — hour-grain (24 buckets) and day-grain (1
   bucket) peak/avg rollups, reusing `test_range_queries.py`'s primitive.
   Named explicitly since the problem statement calls out minute/hour/day
   as three separate graded grains.

9. **`test_ledger.py`** — `session_runs`, the v1-design audit-ledger table
   that would have kept both session identity and active-interval
   semantics for exact per-session cross-checks. **Dropped in v2** — no
   replacement table exists, so every check in this file is a permanent
   SKIP. Kept in the suite (rather than deleted) so the gap stays visible
   run after run instead of silently disappearing.

10. **`test_query_performance.py`** — runs representative benchmark queries
    with a known `query_id`, flushes `system.query_log` cluster-wide, and
    asserts on `read_rows`/`read_bytes`/`query_duration_ms` — catches a query
    that secretly full-scans instead of reading a small serving-layer slice.
    Includes a negative control (a real raw-table scan) to prove the ceiling
    actually distinguishes scan from seek.

11. **`test_edge_cases.py`** — operationalizes `Docs/EDGE_CASES.md`'s 25
   edge cases + the #0 critical bug (intra-minute flapping) as live checks:
   naive-delta vs correct peak (negative control), terminal-state absorption
   of late events (SessionEnd and VideoError both — see below),
   `AppForegrounded`-alone non-activation, sparse-delta gap-minute
   correctness, SI phantom-audience evidence, dimension pinning under
   drift, timeout-closes-mismatched-BG/FG, day-boundary partitioning,
   marathon-session capping, near-zero-duration-session run-length bound.
   File header has the full coverage matrix — which edge case maps to which
   test, which are covered by other files in this suite (`test_ledger.py`'s
   exact per-session run-count match is strictly stronger than anything an
   aggregate-count test here could add for the duplicate-transition rules,
   #1/#2), and which are informational-only / N/A to the design.

   Two edge cases were live-verified against `ch_hackathon_raw_data` and
   fixed rather than shipped ambiguous: a naive "no VideoPlay ever" read of
   #20 (zero-duration sessions) matched 0 real sessions and asserted the
   wrong invariant besides (LLD's state machine gives near-zero sessions a
   short run, not zero rows — fixed to test run *length* instead); #10
   (VideoError terminality) — both LLD-sam.md (no switch effect) and
   EDGE_CASES.md (deactivates, explicitly not terminal) disagree with the
   actual pipeline behavior, confirmed with the team: VideoError **is**
   terminal/absorbing, same as VideoSessionEnd. Tested as such (`ended=1`,
   folded into the terminal-absorption check). Both design docs are stale
   on this point and should be updated to match.

12. **`test_minute_series.py`** — the full per-minute actual-vs-computed
    curve for the event day's sustained-peak hour (10:00–11:00 UTC on
    2026-07-26), not just range-level peak/avg. Catches single-minute spikes
    that range-level aggregates smooth over — see `Docs/CONCURRENCY_VALIDATION.md`
    for a worked example (a +165% single-minute spike right at the ramp-up
    boundary that range-level checks alone don't surface).

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

`ch_client.py` reads `CH_URL` / `CH_USER` / `CH_PASS` / `CH_DATABASE` env vars,
falling back to `rohitdevtesting` (the deployed v2 pipeline) and the
hackathon-provided connection defaults. `RAW_EVENTS`/`CONTENT_DIM` in
`table_names.py` are always fully qualified as `default.*` since the raw seed
tables live in `default` regardless of which serving database is targeted.
Override for a different ClickHouse service or database (e.g. the unseen-day
run):

```
CH_URL=... CH_USER=... CH_PASS=... CH_DATABASE=... ./run.sh
```
