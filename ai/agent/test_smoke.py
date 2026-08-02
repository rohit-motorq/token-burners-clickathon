"""Self-check, no network: router classification + tool schema/dispatch
consistency. Run: python -m src.agent.test_smoke"""
from .router import classify
from .agent import TOOL_SCHEMAS, GENRE_TOOLS, _dispatch


def test_router():
    cases = [
        ("What was peak concurrency on Android in the last hour?", "LOOKUP"),
        ("Is concurrency on sports content rising or falling right now?", "TREND"),
        ("How many billable impressions did advertiser 42 get 8-9pm?", "BILLING"),
        ("Why did concurrency drop 40% on content 1001 in the last 10 minutes?", "DIAGNOSTIC"),
    ]
    for q, expected in cases:
        got = classify(q)
        assert got == expected, f"{q!r} -> {got}, expected {expected}"


def test_genre_tools_exist_in_schema():
    for genre, tool_names in GENRE_TOOLS.items():
        for name in tool_names:
            assert name in TOOL_SCHEMAS, f"{genre} references undefined tool {name}"


def test_dispatch_rejects_unknown_tool():
    try:
        _dispatch("not_a_real_tool", {})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_billing_genre_is_isolated():
    assert GENRE_TOOLS["BILLING"] == ["get_billable_impressions"], \
        "billing genre must expose exactly one tool — no other numeric source"


if __name__ == "__main__":
    test_router()
    test_genre_tools_exist_in_schema()
    test_dispatch_rejects_unknown_tool()
    test_billing_genre_is_isolated()
    print("ok")
