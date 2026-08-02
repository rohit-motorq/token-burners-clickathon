"""MCP server exposing the concurrency tool catalog as typed MCP tools.

Separate from ClickHouse's own official MCP server (which exposes raw SQL
execution) — that one is for debugging/exploration only and should never be
wired to a user-facing chat surface. This server is the one LibreChat (or any
MCP client) should point at for the actual concurrency agent: every tool here
is parameterized, none of them accept raw SQL, same enforcement as agent.py's
tool-calling loop (see INNER_CONTEXT.md — "why the LLM never writes SQL").

Run: python -m src.mcp_server.server
"""
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.agent.tools import billing, capacity, chart, concurrency, content, health
from src.agent import ch_client

# default port is 8000, which collides with src/agent/server.py's FastAPI —
# 8811 matches src/librechat/librechat.yaml's mcpServers.sonyliv-concurrency.url.
# host=0.0.0.0 so a LibreChat container can reach this from outside localhost.
#
# transport_security: FastMCP's default DNS-rebinding protection only
# allowlists localhost/127.0.0.1/[::1] Host headers. LibreChat runs in a
# Docker container and reaches this server via host.docker.internal — that
# Host header gets rejected by default ("Domain ... is not allowed"), even
# though the request originates from the same machine. Allowlisting it
# explicitly is safe for this local dev/demo setup (not internet-facing);
# revisit if this server is ever exposed beyond localhost/docker-host.
# instructions: agent.py's chat loop grounds every question with real
# reference-time + valid dim values injected straight into the system
# prompt (see _reference_now/_known_dim_values there) — an MCP client like
# LibreChat never sees that, since MCP only exposes tools, not a system
# prompt. Without it, confirmed bug: "last hour" / "Android" resolve
# against the calling LLM's own idea of the current date and plausible
# dim values instead of this dataset's, and every such query silently
# returns zero rows. get_dataset_info exists so the calling model can pull
# the same grounding an MCP client's instructions can point it at.
mcp = FastMCP(
    "sonyliv-concurrency", host="0.0.0.0", port=8811,
    instructions=(
        "This is a replayed/synthetic dataset, not live data — call "
        "get_dataset_info first, before any other tool, to learn the real "
        "time range and valid platform/video_type/country values. Never "
        "guess a relative time ('last hour', 'right now') against the real "
        "calendar date, and never guess a dimension value ('Android', "
        "'sports') — both silently return zero rows if wrong. Resolve "
        "every relative time against get_dataset_info's max_event_ts, and "
        "filter dimensions only with the exact values it returns."
    ),
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
                          "http://host.docker.internal:*"],
    ),
)


@mcp.tool()
def get_dataset_info() -> dict:
    """Real max event timestamp + valid platform/video_type/country values
    for this dataset. Call this FIRST, before any other tool — this is a
    replayed dataset, not live, so "now"/"last hour" must resolve against
    max_event_ts here, never the real calendar date, and dimension filters
    must use one of the exact values listed here, never a guessed one
    (e.g. "Android" or "sports") — either mistake silently returns zero
    rows from every other tool."""
    mx_rows = ch_client.query("SELECT max(event_ts) AS mx FROM fact_events")
    mx = mx_rows[0]["mx"] if mx_rows and mx_rows[0].get("mx") else None
    platforms = ch_client.query("SELECT DISTINCT platform FROM fact_concurrency_deltas")
    video_types = ch_client.query("SELECT DISTINCT video_type FROM dim_content")
    countries = ch_client.query("SELECT DISTINCT country FROM fact_concurrency_deltas")
    return {
        "max_event_ts": mx.split(".")[0] if mx else None,
        "valid_platforms": [r["platform"] for r in platforms],
        "valid_video_types": [r["video_type"] for r in video_types],
        "valid_countries": [r["country"] for r in countries],
        "note": "category is a separate opaque code, not enumerated here — "
                "look up a specific content_id's category via get_content_metadata.",
    }


@mcp.tool()
def get_concurrency_curve(platform: str = None, country: str = None, video_resolution: str = None,
                           video_type: str = None, category: str = None, content_id: int = None,
                           start: str = None, end: str = None, grain: str = "minute") -> list:
    """Concurrency curve for a time range + optional dimension filter. grain: minute|hour|day."""
    dims = {k: v for k, v in {"platform": platform, "country": country,
                               "video_resolution": video_resolution, "video_type": video_type,
                               "category": category, "content_id": content_id}.items() if v is not None}
    return concurrency.get_concurrency_curve(dims, start, end, grain)


@mcp.tool()
def get_peak(platform: str = None, country: str = None, video_resolution: str = None,
             video_type: str = None, category: str = None, content_id: int = None,
             start: str = None, end: str = None, grain: str = "minute") -> dict:
    """Peak concurrency + the bucket it occurred in, for a time range + filter."""
    dims = {k: v for k, v in {"platform": platform, "country": country,
                               "video_resolution": video_resolution, "video_type": video_type,
                               "category": category, "content_id": content_id}.items() if v is not None}
    return concurrency.get_peak(dims, start, end, grain)


@mcp.tool()
def get_trend(platform: str = None, country: str = None, video_resolution: str = None,
              video_type: str = None, category: str = None, content_id: int = None,
              end: str = None, lookback_minutes: int = 10) -> dict:
    """Rate of change (direction/slope/delta_pct) over the last N minute-buckets ending at `end`."""
    dims = {k: v for k, v in {"platform": platform, "country": country,
                               "video_resolution": video_resolution, "video_type": video_type,
                               "category": category, "content_id": content_id}.items() if v is not None}
    return concurrency.get_trend(dims, end, lookback_minutes)


@mcp.tool()
def get_active_users(content_id: int, at_minute: str) -> dict:
    """Distinct users still active at a specific minute, for a content_id."""
    return concurrency.get_active_users(content_id, at_minute)


@mcp.tool()
def predict_load(platform: str = None, country: str = None, video_resolution: str = None,
                  video_type: str = None, category: str = None, content_id: int = None,
                  end: str = None, horizon_minutes: int = 10, lookback_minutes: int = 10) -> dict:
    """Projects concurrency forward from the recent trend and recommends
    scale_up/hold/scale_down. Directional projection, not a capacity model."""
    dims = {k: v for k, v in {"platform": platform, "country": country,
                               "video_resolution": video_resolution, "video_type": video_type,
                               "category": category, "content_id": content_id}.items() if v is not None}
    return capacity.predict_load(dims, end, horizon_minutes, lookback_minutes)


@mcp.tool()
def get_content_metadata(content_id: int) -> dict:
    """Title/video_type/category/show_name + estimated end time for a content_id.
    scheduled_end_ts is ALWAYS an inference from past session activity, never
    a real programming schedule (end_ts_is_estimated is always true, and
    scheduled_end_ts is None until at least one session for this content has
    ended) — relay it as an inference, not a fact."""
    return content.get_content_metadata(content_id)


@mcp.tool()
def get_health_signals(content_id: int, start: str, end: str) -> dict:
    """Error/buffer event rate for a content_id over a time window."""
    return health.get_health_signals(content_id, start, end)


# get_si_sa_gap deliberately not registered here — see agent.py and
# src/agent/tools/validation.py: unavailable under the migrationv2 schema.


@mcp.tool()
def get_billable_impressions(advertiser_id: int, start: str, end: str) -> dict:
    """Billable impressions for an advertiser over a time range — the figure
    to bill the advertiser for."""
    return billing.get_billable_impressions(advertiser_id, start, end)


@mcp.tool()
def render_chart(series: list, title: str = "", x_key: str = "minute",
                  y_key: str = "concurrency", chart_type: str = "line") -> str:
    """Render a time series as a markdown-embeddable chart image."""
    return chart.render_chart(series, title, x_key, y_key, chart_type)


if __name__ == "__main__":
    mcp.run(transport="sse")
