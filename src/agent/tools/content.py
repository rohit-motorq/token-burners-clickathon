"""DIAGNOSTIC genre tool. content_dim is ReplacingMergeTree(updated_at) —
read with ORDER BY updated_at DESC LIMIT 1, never FINAL over the base table.

scheduled_end_ts (migration 009) is derived, not authoritative — no real
programming schedule exists in this dataset. It's populated incrementally by
content_estimated_end_mv from session_runs.run_end, so it's None until at
least one session for that content_id has actually closed. end_ts_is_estimated
is always 1 under this design; the agent must relay it as an inference, not
a fact, per prompts.py's DIAGNOSTIC instructions."""
from ..observability import observe
from .. import ch_client


@observe(as_type="tool")
def get_content_metadata(content_id: int) -> dict:
    sql = """
        SELECT title, video_type, category, scheduled_end_ts, end_ts_is_estimated
        FROM content_dim
        WHERE content_id = {content_id:UInt64}
        ORDER BY updated_at DESC
        LIMIT 1
    """
    rows = ch_client.query(sql, {"content_id": content_id})
    return rows[0] if rows else {}
