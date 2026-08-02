"""BILLING genre — the only genre where the LLM's tool schema exposes params,
never SQL. Templates are a fixed registry; ad_content_map is
migrations-prod/009_ad_content_map.sql. The reconciliation-against-batch-job
piece still doesn't exist (reconciliation_delta_pct is always None below)."""
from ..observability import observe
from .. import ch_client

_TEMPLATE_SQL = """
    SELECT impressions FROM (
        SELECT minute, sum(step_delta) OVER (ORDER BY minute) AS impressions
        FROM (
            SELECT minute, sum(delta_sessions) AS step_delta
            FROM fact_concurrency_deltas
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
    """This is the ONLY billing tool exposed to the LLM — it must never be
    asked to write its own SQL for money-relevant numbers."""
    rows = ch_client.query(_TEMPLATE_SQL, {
        "advertiser_id": advertiser_id, "start": start, "end": end,
    })
    impressions = rows[0]["impressions"] if rows else 0
    return {
        "advertiser_id": advertiser_id,
        "impressions": impressions,
        # ponytail: reconciliation against the nightly deterministic batch job
        # isn't built yet — add once that job exists, compare and flag drift.
        "reconciliation_delta_pct": None,
    }
