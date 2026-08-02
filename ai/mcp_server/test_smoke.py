"""Self-check, no network: MCP server builds and exposes all 8 tools with
matching names to the agent's own tool catalog (src/agent/agent.py's
TOOL_SCHEMAS) — the two surfaces must not drift apart.
Run: python -m src.mcp_server.test_smoke"""
from src.mcp_server.server import mcp

EXPECTED_TOOLS = {
    "get_concurrency_curve", "get_peak", "get_trend", "get_content_metadata",
    "get_health_signals", "get_billable_impressions", "render_chart",
}


def test_all_tools_registered():
    import asyncio
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS, f"got {names}, expected {EXPECTED_TOOLS}"


def test_matches_agent_tool_catalog():
    from src.agent.agent import TOOL_SCHEMAS
    assert EXPECTED_TOOLS == set(TOOL_SCHEMAS.keys()), \
        "MCP server and agent.py tool catalogs have drifted apart"


if __name__ == "__main__":
    test_all_tools_registered()
    test_matches_agent_tool_catalog()
    print("ok")
