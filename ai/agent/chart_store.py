"""In-memory chart cache, keyed by a random id. Exists because chat UIs
(confirmed: LibreChat's react-markdown) sanitize markdown image URLs via a
default urlTransform that strips data: URIs for security — only http(s)
renders. Charts get served back over HTTP instead of embedded inline.
Not persisted across process restarts; fine for a single long-lived demo
process, not meant to survive a redeploy."""
import uuid

_store: dict[str, bytes] = {}


def put(png_bytes: bytes) -> str:
    chart_id = uuid.uuid4().hex
    _store[chart_id] = png_bytes
    return chart_id


def get(chart_id: str) -> bytes | None:
    return _store.get(chart_id)
