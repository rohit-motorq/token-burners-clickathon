# Internal decision log

Not a spec, not a howto — a record of *why*, for decisions that weren't
obvious from the code and would otherwise get re-litigated or silently
drift. Append new entries as decisions get made; don't rewrite history.

---

## Why `ad_content_map` maps advertiser_id → content_id, not advertiser_id → time range

**Decision:** BILLING genre's `get_billable_impressions` joins on content_id
via `ad_content_map` (migration 010), not a bare time-range filter across all
concurrency in that window.

**Why:** Streaming platform runs many pieces of content concurrently. An
8–9pm slot has a cricket match, some show, some movie — all airing the same
hour, each with its own sessions/concurrency tracked separately in
`cc_delta_content`. An advertiser doesn't buy "8-9pm on SonyLIV" as a
blanket — they buy inventory on specific content (pre-roll/mid-roll on the
cricket match, say). A time-range-only query can't tell those apart:

```sql
-- wrong: counts concurrency across ALL content airing in that hour,
-- regardless of which content the advertiser actually bought into
SELECT sum(delta_sessions) FROM cc_delta_content
WHERE minute BETWEEN '20:00' AND '21:00'
```

Two concrete failure modes this causes if content_id is dropped:

- **Two advertisers run ads on different content in the same hour** — X
  sponsors the cricket match, Y sponsors the unrelated show. Time range
  alone can't split that traffic between them; both would get billed the
  same (wrong) total: everyone watching anything, that hour.
- **The same content rotates between multiple advertisers within the
  hour** — ad slots on one piece of content aren't exclusive to one buyer.
  Without content_id (and ideally finer ad-slot tracking), there's no way
  to attribute which sessions saw which advertiser's creative. Time range
  degrades to "everyone who watched anything" — not a billing number.

**When this would be wrong:** if the ad product were a "roadblock" buy —
one advertiser sponsors the entire platform for an hour, no other ads run.
In that case content_id is unnecessary and a bare time-range param on
`get_billable_impressions` is correct and cheaper (drops migration 010
entirely). Went with per-content attribution because it's the more common
ad-serving model and the dataset gives no signal either way — revisit if
the real ad product turns out to be roadblock-style.

**Seed data note:** `ad_content_map`'s seed rows use real `content_id`
values pulled live from `ch_hackathon_content_data` (not made up), so the
mapping lines up with actual session activity once `content_dim`/
`events_raw` are populated from that source. Row `(1002, 20971542)` /
`(1003, 20971542)` deliberately duplicates one content_id under two
advertisers to model the same-content-rotating-sponsors case above —
useful as a test fixture for whatever billing query logic gets built on
top of this table.

---

## Why `content_dim.scheduled_end_ts` is derived from `session_runs`, not a real schedule field

**Decision:** migration 009 adds `scheduled_end_ts` + `end_ts_is_estimated`
to `content_dim`, populated incrementally by `content_estimated_end_mv`
firing on `session_runs` inserts (`run_end` per content_id), not backfilled
from any authoritative programming-schedule source — none exists in this
dataset.

**Why this check matters at all:** DIAGNOSTIC genre answers "why did
concurrency drop." The single most common real reason is boring: the
match/show ended. India vs Australia final airs 7–10pm; at 9:58pm
concurrency is 300K, by 10:04pm it's 40K. Without any end-time signal, the
agent's only remaining checks are `get_health_signals` (error rate — comes
back clean, nothing broke) and `get_si_sa_gap` (pipeline gap — also clean).
Both checks passing leaves the agent guessing "content stopped engaging
viewers" — which reads to a stakeholder as *the broadcast was bad*, when
actually the match just ended on schedule. That's the wrong conclusion at
the exact moment — live sport, dashboard being watched in real time — this
whole system exists to get right.

**Why derived from `session_runs` instead of left unbuilt:** no real
schedule data exists to backfill from. Rather than leave the check
permanently unanswerable (silently degrading every diagnosis to "guess
between system issue and organic decline"), `run_end` of the last closed
session for that content is a genuine, if imperfect, proxy — content
whose sessions all closed around 10:00pm probably ended around 10:00pm.

**Why an MV on `session_runs` instead of a batch recompute:** matches the
system's existing insert-trigger pattern (same shape as `cc_delta_dims_mv`)
and stays incremental — as new session runs close throughout the day, each
content's estimated end time ratchets forward automatically. No full
`content_dim` rebuild, no cron job. Reuses `content_dict` (already built for
enrichment) rather than self-joining `content_dim`, so the MV never needs
to read the base table it's writing into.

**Why `end_ts_is_estimated` is a real column, not just a comment:** the
agent's DIAGNOSTIC prompt must relay this as an inference — "content likely
ended around X, inferred from session data, not the programming schedule"
— never as a stated fact. A cricket match scheduled for 10:00pm that goes
to a Super Over and actually ends at 10:22pm means the derived value is
*right* (sessions did close around 10:22pm) but would be *wrong* if
presented as "the scheduled end time," since it isn't one. The flag is what
lets the prompt caveat correctly instead of overclaiming certainty it
doesn't have.

**Known gap:** `scheduled_end_ts` is `NULL` until at least one session for
that content_id has actually closed — for content still airing with no
closed sessions yet, this check can't fire at all, and the agent must treat
null as "unknown," not as "still running." Revisit if a real schedule field
ever becomes available in the dataset — that should take priority over this
derived value when both exist.

---

## Connecting the MCP server to a real LibreChat instance — two allowlists, not one

**Decision:** `src/mcp_server/server.py`'s `FastMCP` gets an explicit
`transport_security` allowing `host.docker.internal:*`, and
`src/librechat/librechat.yaml` gets a top-level `mcpSettings.allowedDomains:
[host.docker.internal]`.

**Why two separate fixes were needed:** connecting a real, already-running
LibreChat instance (Docker, `registry.librechat.ai/danny-avila/librechat-dev`)
to our MCP server surfaced two independent DNS-rebinding/SSRF guards, both
triggered by the same symptom (`Domain "http://host.docker.internal:8811" is
not allowed`), from two different codebases:

1. **MCP SDK's own `TransportSecurityMiddleware`** (`mcp/server/transport_security.py`)
   — validates the *Host header* on incoming requests to our server, default
   allowlist is `127.0.0.1`/`localhost`/`[::1]` only. LibreChat's container
   reaches us via `host.docker.internal`, which isn't in that list by
   default.
2. **LibreChat's own `MCPServersRegistry`** (`packages/api/src/mcp/registry/`)
   — a *separate* SSRF-protection allowlist, checked before LibreChat even
   attempts to connect, keyed off `mcpSettings.allowedDomains` in
   `librechat.yaml`. Empty by default → SSRF protection engages → any
   internal-looking domain (including `host.docker.internal`) gets rejected
   client-side, before the MCP handshake is even attempted.

Fixing only one left the other still rejecting — had to add both. Confirmed
working: `docker logs LibreChat` shows `[MCP] Initialized with 1 configured
server and 7 tools`, listing every tool in `src/agent/tools/` (minus
`get_si_sa_gap`, per the earlier decision).

**How LibreChat was actually wired up (concrete, not hypothetical):**
- LibreChat checkout: `/Users/keshavaneja/experiments/LibreChat`, run via its
  own `docker-compose.yml` (prebuilt image, not built from that checkout's
  source — so a `librechat.yaml` dropped on the host filesystem there does
  NOT appear inside the container without an explicit bind mount).
- Added the mount in `docker-compose.override.yml` (the file LibreChat's own
  `docker-compose.yml` explicitly says to use instead of editing it
  directly): bind `src/librechat/librechat.yaml` (this repo, absolute host
  path) → `/app/librechat.yaml` in the container, read-only.
- `extra_hosts: host.docker.internal:host-gateway` was already present on
  LibreChat's `api` service — didn't need to add it.
- MCP server run via a dedicated persistent venv (`.venv-mcp-server/` at this
  repo's root, gitignored) — the throwaway `/tmp/.venv-*` pattern used for
  every other smoke test in this session doesn't fit a long-running server
  that needs to survive across turns.
- `docker compose up -d --force-recreate api` is what actually picks up a
  `librechat.yaml`/override change — a plain container restart does not
  re-read a new bind mount.

---

## Why `render_chart`'s tool schema has no `series` parameter

**Decision:** the model calls `render_chart(title?, chart_type?)` only —
`agent.py`'s `_dispatch` injects the actual data via a `chart_context` dict
threaded through the tool-calling loop, populated whenever
`get_concurrency_curve`/`get_trend` runs.

**Why:** found via a real LibreChat "empty message" bug report, reproduced
directly against `agent.answer()`: asking "peak concurrency on ANDROID_PHONE
in the last hour" through LibreChat's UI returned nothing. Traced turn-by-turn
against the raw Anthropic response: turn 0 called `get_peak` +
`get_concurrency_curve` (fine); turn 1 tried to call `render_chart` but hit
`stop_reason=max_tokens` with an **empty tool_use block and no text at
all** — because the original tool schema required `series` (the full curve,
potentially hundreds of rows) as a literal tool-call argument, meaning the
model had to regenerate that entire array as JSON inside its own output.
Combined with a "thinking" block and two prior tool calls already in the
same turn budget, `max_tokens=1024` ran out mid-argument. `agent.py`'s loop
then saw `stop_reason != "tool_use"` and returned only the (empty) text
blocks — an empty final answer, with a real, correct answer sitting
unreachable in the tool-call history.

This is the same underlying mistake as the earlier `render_chart`-output
bug (asking the model to reproduce a big base64 image string) — asking an
LLM to copy data verbatim through its own token budget, in either direction,
is unreliable and wasteful. Both output and input are now handled outside
the model's token budget: output via `chart_images` appended post-hoc
(existing fix), input via `chart_context` injected pre-hoc (this fix).

**Known gap this doesn't cover:** `src/mcp_server/server.py`'s `render_chart`
MCP tool still takes a `series` parameter — LibreChat's own native MCP tool
loop calls it directly, with no equivalent `chart_context` threading on that
path. Same failure mode is possible there; not fixed, since MCP tools are
stateless per-call with no shared session state to inject from without
building session-keyed server-side caching. Flagged in `REMAINING_WORK.md`.

---

## Why chart images are served over HTTP (`chart_store.py` + `/charts/{id}.png`), not embedded as `data:` URIs

**Decision:** `agent.py`'s `render_chart` dispatch calls
`chart.render_chart_png()` (raw bytes), stashes them in an in-memory
`chart_store` keyed by a random id, and returns
`![title](http://localhost:8000/charts/{id}.png)` instead of a base64
`data:image/png;base64,...` URI.

**Why:** the text half of an answer rendered fine in LibreChat, but the
image never appeared. Root cause: react-markdown (which LibreChat uses)
ships a default `urlTransform` that sanitizes markdown image/link URLs,
stripping anything that isn't `http(s)`/`mailto`/`tel` — `data:` URIs get
silently removed, which is exactly why the `![...]​(data:image/png;base64,...)`
markdown from the earlier fix rendered as an empty/broken image with no
error anywhere in the pipeline. Confirmed by finding `urlTransform` in
LibreChat's built client bundle and cross-referencing react-markdown's
documented default sanitization behavior.

**Why `http://localhost:8000`, not `http://host.docker.internal:8000`:** the
chart `<img src>` URL is fetched by the **user's browser**, not by
LibreChat's backend container. The browser runs on the host machine, where
the agent server is directly reachable at `localhost:8000` — `host.docker.internal`
is a container-to-host resolution mechanism, irrelevant here since nothing
inside a container is fetching this URL. `AGENT_PUBLIC_BASE_URL` in
`config.py` makes this explicit and overridable.

**Verified concretely** (not just "should work"): POSTed a real question to
the running `/v1/chat/completions`, extracted the returned image URL,
`curl`'d it back — `200 OK`, `content-type: image/png`, valid 770×330 PNG
matching the chart described in the same reply's text.

**Two render_chart implementations, deliberately:** `chart.py` keeps both
`render_chart_png` (bytes, used by `agent.py`'s HTTP-serving path) and
`render_chart` (base64 markdown, used by `mcp_server/server.py`'s MCP tool,
which has no HTTP-serving mechanism of its own and no `chart_store`
threading). The MCP path still has the data-URI problem if whatever client
calls it also sanitizes markdown the same way — not fixed there, see the
`render_chart` schema entry above for why (stateless per-call MCP tools,
no shared session to serve images from without more machinery).

---

## Why `mcp` is pinned `<2.0` in `src/mcp_server/requirements.txt`

**Decision:** pinned, not left open-ended.

**Why:** `pip install mcp` picks up 2.0.0 by default, which renamed
`FastMCP` to `MCPServer` and moved it from `mcp.server.fastmcp` to
`mcp.server.mcpserver` — a fresh, sparsely-documented major version.
`src/mcp_server/server.py` is written against the 1.x `FastMCP` API (widely
documented, matches what most MCP/LibreChat integration guides reference).
Installing without the pin breaks the import with no obvious error message
tying it back to a version bump. Bump the pin deliberately if the 2.0 API
stabilizes and there's a reason to move — not as a side effect of a bare
`pip install -U`.

---

## Why `langfuse` is pinned `>=4.0,<5.0`, and what "proper" tracing means here

**Decision:** built against Langfuse SDK v4 (OTel-based), not the `langfuse.decorators`
API the original plan sketched code against.

**Why:** same story as the `mcp` pin — `pip install langfuse` gives you
whatever's latest, and as of this build that's v4, a full rewrite. `from
langfuse.decorators import observe, langfuse_context` (what earlier drafts
used) doesn't exist anymore — the module was removed. v4's replacements:
`from langfuse import observe, get_client, propagate_attributes`. Rather
than pin down to the old API to match stale example code, rebuilt
`observability.py`/`agent.py` against the real v4 surface, since it's what
anyone `pip install`-ing this today actually gets, and it's arguably better
suited to this design:

- `@observe(as_type="tool")` on every function in `tools/*.py` — v4 has a
  real `tool` observation type, not just a generic span, so Langfuse's UI
  shows these as what they are.
- `@observe(as_type="generation")` wraps a new `_call_model` helper in
  `agent.py`, isolating the raw Anthropic call so it carries model/token
  usage/input/output as a proper generation record — previously this was
  just an opaque function call nested inside `answer()`'s span, no model or
  token-usage data attached anywhere.
- `with propagate_attributes(tags=[genre], user_id=..., session_id=..., metadata=...)`
  wraps the tool-calling loop, tagging every span in that trace with the
  router's genre decision — this is what the Phase 4 eval scoring (still
  unbuilt, see `REMAINING_WORK.md`) will filter/group by.
- `user_id` flows from `server.py`'s request (`req.user`, the OpenAI
  schema's end-user field) through to `answer()` — lets Langfuse attribute
  traces to whichever LibreChat end-user asked the question, not just an
  anonymous pile of traces.

**Why no custom no-op shim is needed:** v4's `get_client()` degrades
gracefully on its own — logs one warning, returns a disabled client, never
raises — when `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` aren't set. Confirmed
by running the full eval harness with no keys configured: every decorator
and context manager ran through cleanly, all 4 questions classified
correctly, and the only failure was the expected one (no `ANTHROPIC_API_KEY`
in this environment either). The `observability.enabled` flag added on top
just avoids two redundant explicit `get_client()` calls
(`update_current_generation`, `flush()`) when tracing is off — the
decorators themselves still log their own one-line "disabled" warning
regardless, which is the SDK correctly telling you it's off, not a bug worth
suppressing further.

---

## Why `agent.py` injects a "reference now" into the system prompt, derived from the data

**Decision:** `_reference_now()` queries `max(event_ts)` from `events_raw` and
injects it into every system prompt as the timestamp "now"/"last hour"/
"yesterday" should resolve against.

**Why:** discovered by running the eval harness with a real model for the
first time (previously blocked on a missing `ANTHROPIC_API_KEY`). Every
LOOKUP/TREND/DIAGNOSTIC question uses relative time ("last hour," "right
now," "yesterday") — but this is a replayed/synthetic dataset frozen at
2026-07-26 11:30, not a live feed, and the model has no way to know that.
Without this injection, the model guessed plausible-looking real-world
dates (2024, 2025) for "now," every relative-time query silently returned
zero rows, and the model correctly reported "no data" — technically honest,
completely wrong reason.

**A second bug this surfaced:** `event_ts` is `DateTime64(3)` (carries
milliseconds); the first version of `_reference_now()` returned that
verbatim (e.g. `2026-07-26 11:30:04.847`), which the model then echoed back
as a tool argument. Every tool's SQL binds `start`/`end` as plain `{..:DateTime}`
(whole seconds only) — ClickHouse 500s trying to parse a fractional-second
string into that type. Result: `get_trend` and `get_concurrency_curve` both
threw on nearly every real question, and the agent burned all `MAX_TURNS`
retrying variations before giving up. Fixed by truncating to whole seconds
in `_reference_now()` itself (`mx.split(".")[0]`) rather than widening every
tool's param type — this is the only place a sub-second timestamp enters the
system, so it's the cheaper, more targeted fix.

**Verification note:** confirmed via a genuine live run (not just schema
checks) — before the fix, LOOKUP and DIAGNOSTIC genres hit `MAX_TURNS`
without a final answer and TREND hit a raw ClickHouse 500; after, all 4
genres return coherent, correct-looking answers against real data, with far
lower latency (no wasted retry turns). Also caught along the way: the
original DIAGNOSTIC eval fixture (`content_id=20971542`) genuinely has zero
rows in `cc_delta_content` — a real, correct "no data" answer, but one that
never exercised the actual reasoning chain. Swapped to `2078157818` (306
rows, confirmed live) so the fixture demonstrates the full curve → metadata
→ health signal chain instead of an early exit.

---

## Why the LLM never writes SQL, for any genre

**Decision:** every tool in `src/agent/tools/` takes typed params and runs a
fixed, parameterized query — the agent's tool-calling schema never exposes
a "run this SQL" tool, not even for exploration.

**Why:** started as a billing-specific guardrail (a subtly wrong JOIN or
missed filter changes money owed), but generalizing it to every genre is
strictly better: same enforcement point (`ch_client.query` is the only
thing that talks to ClickHouse, and only tools call it), same Langfuse
tracing story (every DB access is a named, typed span — "get_trend" means
something, an arbitrary SELECT doesn't), and it removes SQL-injection
surface entirely rather than trusting prompt-injection defenses. Cost is
some flexibility — the agent can only ask questions the tool catalog
anticipated — judged worth it given billing is in scope.

---

## Why `cc_delta_content`/`cc_delta_dims` queries filter on `minute` first, not `content_id`/`platform` first

**Decision:** all tool SQL in `concurrency.py`/`validation.py` filters on
`minute` range before dimension filters, matching the actual migrated
ORDER BY — `(minute, content_id, platform, country, video_type, category)`
on `cc_delta_content`, `(minute, platform, country, video_type, category)`
on `cc_delta_dims`.

**Why:** this is what's actually in `src/migrations/004`/`005` — differs
from the original LLD draft, which put content_id/platform first for
drill-down prefix-seeks. The real schema optimizes for "give me the curve
for a time range, then filter dims," which fits LOOKUP/TREND/DIAGNOSTIC
(always time-bounded) at the cost of content_id drill-downs no longer
being a pure prefix seek. Acceptable at this scale; flag if judges ask why
minute is first.
