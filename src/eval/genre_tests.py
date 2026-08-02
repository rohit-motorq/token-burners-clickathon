"""One fixture per genre + expected tool-call sequence. Runs against the real
agent.answer() — same code path src/agent/server.py uses, real ClickHouse,
real (if configured) LLM — not a mock. This IS the pipeline evidence for the
unseen-day requirement: rerun against the sealed dataset, same harness, same
report shape.

Run: python -m src.eval.genre_tests
Writes src/eval/eval_report.json with per-case latency + tool-call sequence.
"""
import json
import time
from pathlib import Path

from ..agent.agent import answer
from ..agent.router import classify
from ..agent.observability import get_client, enabled as _langfuse_enabled

# Fixtures verified against real rows in rohitdevtesting's fact_concurrency_deltas
# (checked live, see INNER_CONTEXT.md). Earlier versions used "Android" and
# "sports" — neither exists as a literal value in this dataset (real
# platform values are ANDROID_PHONE/IPHONE/SONY_ANDROID_TV/...; category
# values are all obfuscated strings like "cdbgg", no genre labels at all) —
# those fixtures silently exercised the "no data" path, not the real one.
CASES = [
    {
        "name": "lookup_peak_android",
        # ANDROID_PHONE confirmed 4395 rows in the 10:30-11:30 window (the
        # dataset's own "last hour", see agent.py's _reference_now).
        "question": "What was peak concurrency on ANDROID_PHONE in the last hour?",
        "expected_genre": "LOOKUP",
        "expected_tools": ["get_peak"],
    },
    {
        "name": "trend_live_rising_falling",
        # "sports" doesn't exist as a category anywhere in this dataset;
        # video_type='live' is a real value (679 rows) and the closest
        # honest analog available.
        "question": "Is concurrency on live content rising or falling right now?",
        "expected_genre": "TREND",
        "expected_tools": ["get_trend"],
    },
    {
        "name": "billing_advertiser_slot",
        # advertiser 1002 -> content_id 2078157818 (migration 011, reseeded),
        # confirmed 260 real impressions in this exact window.
        "question": "How many billable impressions did advertiser 1002 get between 10am and 11am on 2026-07-26?",
        "expected_genre": "BILLING",
        "expected_tools": ["get_billable_impressions"],
    },
    {
        "name": "diagnostic_content_drop",
        # 2078157818 has 306 rows in fact_concurrency_deltas (checked live) — real
        # activity, unlike the earlier 20971542 (0 rows, valid data but never
        # exercised the actual reasoning chain).
        "question": "Why did concurrency drop 40% on content 2078157818 in the last 10 minutes?",
        "expected_genre": "DIAGNOSTIC",
        "expected_tools": ["get_concurrency_curve", "get_content_metadata"],
    },
]


def _is_subsequence(expected: list, actual: list) -> bool:
    it = iter(actual)
    return all(name in it for name in expected)


def run() -> list[dict]:
    results = []
    for case in CASES:
        trace: list = []
        t0 = time.perf_counter()
        row = {"name": case["name"], "question": case["question"]}
        try:
            genre = classify(case["question"])
            row["genre_ok"] = genre == case["expected_genre"]
            row["genre_got"] = genre
            reply = answer(case["question"], _trace=trace)
            row["reply"] = reply
            row["tool_trace"] = trace
            row["tools_ok"] = _is_subsequence(case["expected_tools"], trace)
            row["error"] = None
        except Exception as e:
            row["error"] = str(e)
            row["genre_got"] = row.get("genre_got")
            row["tool_trace"] = trace
            row["tools_ok"] = False
        row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        results.append(row)
    if _langfuse_enabled:
        get_client().flush()  # short-lived script — force traces out before exit
    return results


if __name__ == "__main__":
    results = run()
    out_path = Path(__file__).parent / "eval_report.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    for r in results:
        status = "OK" if not r["error"] and r.get("genre_ok") and r.get("tools_ok") else "FAIL"
        print(f"[{status}] {r['name']} ({r['latency_ms']}ms) — {r.get('error') or ''}")
    print(f"\nWrote {out_path}")
