"""Genre classifier. Rule-based only — no LLM call, cheap and deterministic.
ponytail: add an LLM fallback for ambiguous phrasing when a real question
proves the regexes wrong; not needed to demo the 4 known genres."""
import re

GENRES = ("BILLING", "TREND", "DIAGNOSTIC", "LOOKUP")

_BILLING_RE = re.compile(r"\b(advertiser|billable|impressions?|revenue|invoic\w*)\b", re.I)
_TREND_RE = re.compile(r"\b(rising|falling|trend|rate of change|how fast|accelerat\w*|declin\w*)\b", re.I)
_DIAGNOSTIC_RE = re.compile(r"\b(why|explain|dropped?|drop|caused?|reason)\b", re.I)


def classify(question: str) -> str:
    if _BILLING_RE.search(question):
        return "BILLING"
    if _DIAGNOSTIC_RE.search(question):
        return "DIAGNOSTIC"
    if _TREND_RE.search(question):
        return "TREND"
    return "LOOKUP"
