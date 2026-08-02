"""DIAGNOSTIC genre tool. dim_content is ReplacingMergeTree(updated_at) —
read with ORDER BY updated_at DESC LIMIT 1, never FINAL over the base table.

scheduled_end_ts/end_ts_is_estimated (migrations-prod/008_content_estimated_end.sql,
ported from migrationv2's migration 010) is derived, not authoritative — no real
programming schedule exists in this dataset. It's populated incrementally by
mv_content_estimated_end from fact_concurrency_deltas' last observed
deactivation (delta_sessions = -1) per content_id, so it's None until at
least one session for that content_id has actually ended. end_ts_is_estimated
is always 1 under this design; the agent must relay it as an inference, not
a fact, per prompts.py's DIAGNOSTIC instructions."""
from ..observability import observe
from .. import ch_client


@observe(as_type="tool")
def get_content_metadata(content_id: int) -> dict:
    sql = """
        SELECT title, video_type, category, show_name, scheduled_end_ts, end_ts_is_estimated
        FROM dim_content
        WHERE content_id = {content_id:UInt64}
        ORDER BY updated_at DESC
        LIMIT 1
    """
    rows = ch_client.query(sql, {"content_id": content_id})
    return rows[0] if rows else {}
