# Remaining work

Punch list of everything still open, outside of Phase 6 (being implemented
next). Not a decision log — see `src/agent/INNER_CONTEXT.md` for the *why*
behind choices already made. This is just what's left.

## Phase 4 — Langfuse eval scoring

Tracing itself is done: `@observe(as_type="tool"|"generation")` on every tool
+ the model call, `propagate_attributes` tags every span with the router's
genre + user_id/session_id (see `INNER_CONTEXT.md` for the v4 API rewrite
this required). What's still missing is actually *scoring* a trace —
`src/eval/run_scores.py` with the per-genre rubric functions from the
original plan: `score_diagnostic_order` (did it check metadata -> health ->
SI/SA in order — the `_trace` list `agent.answer()` already returns makes
this easy to check locally, but a Langfuse-side score via
`get_client().score_current_trace` or the batch scoring API is what makes it
visible/filterable in the Langfuse UI), `score_billing_guardrail` (only
called `get_billable_impressions`, disclaimer present in output). Run as a
batch job against recent Langfuse traces, not real-time.

Not yet verified: an actual trace has never been seen in a real Langfuse
project — no `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` configured in this
environment. Everything above is confirmed correct against the SDK's real
API surface and confirmed to degrade gracefully without keys; it is not
confirmed to render correctly in the actual Langfuse dashboard.

## Phase 7 — ClickStack health signals

`tools/health.py` is a placeholder — computes error/buffer rate straight from
`events_raw` (`VideoError`/`BufferStart` event counts) because no ClickStack
otel table exists yet. Swap the query once ClickStack is actually stood up
and its ingested schema is known; same return shape (`error_rate`,
`buffer_rate`, `total_events`), just a different FROM/WHERE.

## Migration 011 — `cc_users_minute` (optional)

Distinct-people-not-streams table from the original HLD, never migrated.
Not blocking any of the 4 in-scope genres (LOOKUP/TREND/BILLING/DIAGNOSTIC)
— only build if a benchmark query specifically needs unique-viewer counts
instead of session counts.

## Billing reconciliation job

`billing.py`'s `get_billable_impressions` always returns
`reconciliation_delta_pct: None` — the nightly deterministic batch job that's
supposed to re-run the same template against finalized data and flag drift
doesn't exist yet. Needed before BILLING genre's guardrail story is complete
(currently: template-only + disclaimer, but no actual cross-check).

## Security/hygiene

- `src/migrations/001_content_dim.sql:21` — hardcoded ClickHouse password in
  plaintext SQL, committed to the repo. Swap for env-var substitution at
  migration-apply time before this repo goes in front of judges.
- `Base/SonyLiv/evals/ch_client.py` has the same pattern (hardcoded creds) —
  pre-existing, not introduced by this work, but same fix applies.

## RESOLVED: `src/migrationv2/` schema conflict

Was flagged as unreconciled risk; now resolved. `migrationv2` (targeting the
`rohitdevtesting` database, which has real ingested data — 33K content rows,
900K+ events) is the authoritative pipeline going forward, per explicit
direction. `src/agent/config.py`'s `CH_DATABASE` now defaults to
`rohitdevtesting`. Tool-level changes this required (see `INNER_CONTEXT.md`
for full reasoning):

- `concurrency.py` — dropped the `cc_delta_dims` branch entirely (table
  doesn't exist under migrationv2); every query reads `cc_delta_content`
  directly, even without a content_id filter.
- `validation.py`'s `get_si_sa_gap` — unregistered from `agent.py`'s
  `GENRE_TOOLS`/`TOOL_SCHEMAS` and `mcp_server/server.py`'s tool list (kept
  as dead code in the file only). `cc_si_minute` doesn't exist under
  migrationv2 — there is no session-independent presence signal at all under
  this schema, only a single `is_active` state. `prompts.py`'s DIAGNOSTIC
  step 4 (SI/SA check) dropped accordingly.
- New `src/migrationv2/migrations/010_content_estimated_end.sql` +
  `011_ad_content_map.sql` — additive patches on top of migrationv2, applied
  live to `rohitdevtesting`. 010 re-sources the `scheduled_end_ts` estimate
  from `cc_delta_raw` (negative-delta rows) instead of `session_runs` (which
  doesn't exist under this pipeline) — same idea, different source table.
  011 is unchanged from `src/migrations/010` (no pipeline dependency).
- `src/migrations/` (the original 8 + 009/010) still exists and is still
  correct for the `default` database, where it was originally applied and
  where `content_dim`/`cc_delta_content` happen to be byte-identical to
  migrationv2's versions. Left as-is, not deleted — just no longer the
  active target.

## Ops — now verified end-to-end (previously open)

- `ANTHROPIC_API_KEY` added, real model round-trips confirmed working for
  all 4 genres against real ClickHouse data on `rohitdevtesting`.
- Two real bugs found and fixed via this: (1) the agent had no sense of
  "now" — fixed by injecting `max(event_ts)` from the data as the reference
  time for resolving "last hour"/"yesterday"/etc; (2) that reference time
  initially carried milliseconds (`event_ts` is `DateTime64(3)`), which
  broke every tool's plain `{..:DateTime}` param binding with a ClickHouse
  500 — fixed by truncating at the source. See `INNER_CONTEXT.md`.
- `src/eval/eval_report.json` now shows 4/4 genuine passes: correct genre
  routing, correct tool-call order, coherent answers, charts rendering.

## Ops — still not verified end-to-end

- No live LibreChat instance exists in this repo or was tested against
  `librechat.yaml` — config is written to LibreChat's documented schema but
  unverified against a running LibreChat.
- No live MCP `sse` client connection tested — confirmed the server
  constructs and registers all 7 tools (8 minus `get_si_sa_gap`), not that a
  real client can connect over the wire and call one.
- No actual trace has been seen in a real Langfuse project — no
  `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` configured in this environment.
- Every Python dependency check so far used a throwaway venv, deleted after
  each smoke test — no persistent installed environment, no lockfile.
