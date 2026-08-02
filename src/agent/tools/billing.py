"""BILLING genre — the only genre where the LLM's tool schema exposes params,
never SQL. Templates are a fixed registry; the reconciliation table and
ad_content_map (migration 010) don't exist yet on a fresh instance, so this
raises a clear error until that migration lands rather than silently
returning a wrong number."""
from ..observability import observe
from .. import ch_client

DISCLAIMER = "Estimate from the serving layer, not the invoicing pipeline. Not for billing/invoicing use."

_TEMPLATE_SQL = """
    SELECT impressions FROM (
        SELECT minute, sum(step_delta) OVER (ORDER BY minute) AS impressions
        FROM (
            SELECT minute, sum(delta_sessions) AS step_delta
            FROM cc_delta_content
            WHERE content_id IN (
                    SELECT content_id FROM ad_content_map WHERE advertiser_id = {advertiser_id:UInt64}
                  )
            GROUP BY minute
        )
    )
    WHERE minute >= {start:DateTime} AND minute < {end:DateTime}
    ORDER BY minute DESC
    LIMIT 1
"""
# note: the time filter is applied to the cumulative sum's *output*, never
# inside the subquery the running sum is computed over — filtering before
# the window function resets the count to 0 at `start` instead of carrying
# forward concurrency already in progress (same bug as concurrency.py, see
# Docs/CONCURRENCY_VALIDATION.md Finding 1).


@observe(as_type="tool")
def get_billable_impressions(advertiser_id: int, start: str, end: str) -> dict:
    """Requires migration 010 (ad_content_map). This is the ONLY billing tool
    exposed to the LLM — it must never be asked to write its own SQL for
    money-relevant numbers."""
    rows = ch_client.query(_TEMPLATE_SQL, {
        "advertiser_id": advertiser_id, "start": start, "end": end,
    })
    impressions = rows[0]["impressions"] if rows else 0
    return {
        "advertiser_id": advertiser_id,
        "impressions": impressions,
        "disclaimer": DISCLAIMER,
        # ponytail: reconciliation against the nightly deterministic batch job
        # isn't built yet — add once that job exists, compare and flag drift.
        "reconciliation_delta_pct": None,
    }
