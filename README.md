# SonyLIV Concurrency Agent

A tool-calling LLM agent over SonyLIV's foreground-only concurrency data in
ClickHouse — answers dashboard lookups, trend questions, billing estimates,
and diagnostic "why did concurrency drop" questions, via LibreChat or a
plain HTTP endpoint. Built for Click-a-thon 2026.

- **Design reasoning / why things are built this way:** [`src/agent/INNER_CONTEXT.md`](src/agent/INNER_CONTEXT.md)
- **What's still open:** [`REMAINING_WORK.md`](REMAINING_WORK.md)

## Architecture, in one paragraph

`src/agent/tools/*.py` are the actual ClickHouse-querying functions — the
LLM never writes SQL, only calls these with typed parameters. Two surfaces
expose the same tools: `src/agent/server.py` (an OpenAI-compatible HTTP
endpoint, the primary path, with Langfuse tracing) and
`src/mcp_server/server.py` (an MCP server, for LibreChat's native MCP tool
support or any other MCP client). `src/librechat/librechat.yaml` wires both
into LibreChat, plus a second MCP server — the official `mcp-clickhouse`,
read-only, registered as a priority-ranked fallback for analysis our own
tools don't cover (LibreChat's native MCP path only; the primary HTTP path
never touches raw SQL — see `INNER_CONTEXT.md`). ClickHouse schema lives in
`src/migrationv2/migrations/` (the authoritative one — see `INNER_CONTEXT.md`
for why).

## Prerequisites

- Python 3.10+
- A ClickHouse Cloud instance with the `rohitdevtesting` database (or your
  own — see `--dir` on `apply_migrations.py` if targeting a different schema)
- An Anthropic API key
- Docker + an existing [LibreChat](https://github.com/danny-avila/LibreChat)
  checkout, running via its own `docker-compose.yml` — **only if** you want
  the LibreChat integration. The agent itself works without it (see
  "Testing without LibreChat" below).

## Setup

```bash
./scripts/setup.sh
```
Creates `.venv`, installs everything, and scaffolds `src/agent/.env` from
`src/agent/.env.example` if it doesn't exist yet.

Then edit `src/agent/.env` and fill in:
```
CH_PASS=<your ClickHouse password>
ANTHROPIC_API_KEY=<your Anthropic key>
```
(`CH_URL`, `CH_USER`, `CH_DATABASE` already default to the shared hackathon
instance/database — only override if you're pointing at your own.)

```bash
./scripts/apply_migrations.sh
```
Applies `src/migrationv2/migrations/*.sql` against your configured
ClickHouse instance. Every statement is `CREATE ... IF NOT EXISTS` — safe to
re-run.

```bash
./scripts/start_all.sh
```
Starts these in the background:
- Agent HTTP server on `:8000` (`logs/agent_server.log`)
- MCP server on `:8811` (`logs/mcp_server.log`)
- ClickHouse MCP server on `:8812` (`logs/clickhouse_mcp.log`) — **optional**,
  only starts if `src/mcp_server/clickhouse_mcp.env` exists. To enable it:
  ```bash
  cp src/mcp_server/clickhouse_mcp.env.example src/mcp_server/clickhouse_mcp.env
  # edit it, fill in CLICKHOUSE_PASSWORD
  ```

```bash
./scripts/stop_all.sh
```
Stops all of the above.

## Testing without LibreChat

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"concurrency-agent","messages":[{"role":"user","content":"What was peak concurrency on ANDROID_PHONE in the last hour?"}]}'
```

Or run the eval harness (4 fixture questions, one per genre, against real
data — this is also the pipeline-evidence artifact for the unseen-day
benchmark):
```bash
.venv/bin/python -m src.eval.genre_tests
```
Writes `src/eval/eval_report.json` (per-case latency, tool-call sequence,
genre-routing correctness).

## Connecting to LibreChat

Assumes LibreChat is already cloned and running elsewhere via its own
`docker-compose.yml`.

```bash
.venv/bin/python scripts/wire_librechat.py /path/to/your/LibreChat
```
This merges (doesn't overwrite) a bind mount for
`src/librechat/librechat.yaml` into that checkout's
`docker-compose.override.yml`, then prints the exact recreate command:
```bash
cd /path/to/your/LibreChat && docker compose up -d --force-recreate api
```

Confirm it connected:
```bash
docker logs LibreChat --tail 50 2>&1 | grep -iE "mcp|sonyliv|clickhouse"
# with only the concurrency MCP server enabled:
#   [MCP] Initialized with 1 configured server and 7 tools.
# with clickhouse_mcp.env also configured (see start_all.sh above):
#   [MCP] Initialized with 2 configured servers and 10 tools.
```

In the LibreChat UI: start a new chat, open the **model/endpoint
dropdown** (not the sidebar "Agents" panel — that's a different LibreChat
feature, unrelated to this integration) and select **"SonyLIV Concurrency
Agent"**.

Sample questions verified against real data:
- *"What was peak concurrency on ANDROID_PHONE in the last hour?"*
- *"Is concurrency on live content rising or falling right now?"*
- *"How many billable impressions did advertiser 1002 get between 10am and 11am on 2026-07-26?"*
- *"Why did concurrency drop 40% on content 2078157818 in the last 10 minutes?"*

## Troubleshooting

Every one of these was a real bug hit and fixed during development — full
reasoning for each is in [`INNER_CONTEXT.md`](src/agent/INNER_CONTEXT.md).

| Symptom | Cause | Fix already applied |
|---|---|---|
| Empty message in LibreChat, server logs show `200 OK` | LibreChat requests `stream: true`; a flat JSON response doesn't parse as SSE | `server.py` returns a fake single-chunk SSE stream when `stream=true` |
| Empty message, specifically after a chart-producing question | Model tried to copy the full data series into `render_chart`'s tool-call arguments, or the full base64 image into its own reply text, and hit `max_tokens` mid-copy | Chart data is injected server-side (`chart_context`), chart output is attached post-hoc (`chart_images`) — model never copies either |
| Chart never displays, even though the reply text is correct | react-markdown strips `data:` image URIs by default (security) | Charts are served over real HTTP (`/charts/{id}.png`, `chart_store.py`), not embedded as base64 |
| LibreChat: `Domain "http://host.docker.internal:8811" is not allowed` | Two *separate* SSRF/DNS-rebinding guards — the MCP SDK's own `transport_security`, and LibreChat's own `mcpSettings.allowedDomains` | Both allowlist `host.docker.internal` (`mcp_server/server.py` + `librechat.yaml`) |
| LibreChat: "No key found. Please provide a key" | `librechat.yaml`'s custom endpoint had `apiKey: "user_provided"` | Hardcoded to a placeholder string — our server never validates it anyway |
| Casual phrasing (`"Android"`, `"sports"`) silently returns no data | The model guessed a plausible-but-wrong literal value that doesn't exist in this dataset's actual enum values | `agent.py` queries real distinct platform/video_type/country values fresh every request and injects them into the system prompt, so the model maps casual phrasing to the real value instead of guessing |
| Every relative-time question ("last hour", "yesterday") returns empty | The model has no real-world clock and this is a frozen, replayed dataset | `agent.py` injects `max(event_ts)` from the data itself as "now" in the system prompt |
| Reply answers the number but never says which platform/content it's about | Telling the model to drop technical field names also dragged out the plain-language filter context (e.g. never says "on Jio Android TV") | A separate house rule in `prompts.py` requires naming the platform/content/filter in plain words, distinct from the no-jargon rule |
| `ModuleNotFoundError: mcp.server.fastmcp` | `pip install mcp` defaults to v2.0, which renamed/moved that module | `requirements.txt` pins `mcp>=1.2,<2.0` |
| Langfuse import errors (`langfuse.decorators` missing) | `pip install langfuse` defaults to v4 (OTel-based rewrite, different API) | Code is written against the real v4 API (`observability.py`); requirements pin `langfuse>=4.0,<5.0` |
| `mcp-clickhouse`: `Authentication is required for HTTP/SSE transports` | SSE/HTTP transport refuses to start without some auth configured | `CLICKHOUSE_MCP_AUTH_DISABLED=true` in `clickhouse_mcp.env` — fine for this local/docker-host trust boundary, same as our own MCP server |

## Repo layout

```
src/
  migrationv2/migrations/   authoritative ClickHouse schema (+ this project's additions: 010, 011)
  migrations/               earlier schema, still correct for the `default` database, no longer the target
  agent/
    tools/                  the actual query functions — concurrency, content, health, billing, chart, validation (unused)
    agent.py                tool-calling loop
    server.py               OpenAI-compatible HTTP endpoint
    router.py               genre classifier (LOOKUP/TREND/BILLING/DIAGNOSTIC)
    prompts.py              per-genre system prompts
    config.py, ch_client.py, observability.py, chart_store.py
    INNER_CONTEXT.md         decision log — read this before re-litigating a choice
  mcp_server/                MCP tool server
  librechat/librechat.yaml   LibreChat config (custom endpoint + MCP)
  eval/genre_tests.py         4-fixture eval harness, real pipeline evidence
scripts/                     setup, migrations, start/stop, LibreChat wiring
REMAINING_WORK.md             punch list of what's not done yet
```
