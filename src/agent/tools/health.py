"""DIAGNOSTIC genre tool — error rate / heartbeat-gap signal.

ponytail: no ClickStack otel table exists yet (Phase 7 of the plan). Placeholder
computes error/buffer rate straight from events_raw, which already carries
enrichment columns. Swap the FROM/WHERE here for ClickStack's ingested table
once that service is stood up and its schema is known — same return shape."""
from ..observability import observe
from .. import ch_client


@observe(as_type="tool")
def get_health_signals(content_id: int, start: str, end: str) -> dict:
    sql = """
        SELECT
            countIf(event_type = 'VideoError') AS error_events,
            countIf(event_type = 'VideoHeartbeat' AND event = 'BufferStart') AS buffer_events,
            count() AS total_events
        FROM events_raw
        WHERE content_id = {content_id:UInt64}
          AND event_ts >= {start:DateTime}
          AND event_ts < {end:DateTime}
    """
    rows = ch_client.query(sql, {
        "content_id": content_id, "start": start, "end": end,
    })
    row = rows[0] if rows else {"error_events": 0, "buffer_events": 0, "total_events": 0}
    total = row["total_events"] or 1
    return {
        "error_rate": row["error_events"] / total,
        "buffer_rate": row["buffer_events"] / total,
        "total_events": row["total_events"],
    }
