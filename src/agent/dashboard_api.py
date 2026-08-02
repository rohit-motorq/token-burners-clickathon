"""REST endpoints for the dashboard UI. Separate from /v1/chat/completions —
this is plain JSON, no LLM in the loop. Mounted onto the same FastAPI app in
server.py so one uvicorn process serves chat + dashboard + chart images.

Schema is migrations-prod (repo-root migrations-prod/*.sql), not migrationv2:
fact_concurrency_deltas carries platform/country/video_resolution as direct
columns, but NOT video_type/category — those are content-derived and only
reachable via dictGet('dict_content', col, content_id), same as
tools/concurrency.py already does.

fact_concurrency_stats (the pre-grouped per-minute rollup table breakdown
queries would ideally use) is UNRELIABLE on real data: mv_compute_stats's
active_runs/open_runs CTEs default run_end to a 2099-01-01 sentinel for any
session whose close/tombstone event never showed up, and the per-minute
range() explosion on that sentinel either exceeds ClickHouse's 500M-element
array cap or OOMs the query. It has since partially recovered (recent
refreshes are succeeding), but only covers a rolling recent window, not
full history, and can crash again the moment a future refresh hits another
unterminated session — same for the downstream fact_concurrency_stats_hourly
rollup, which reads FROM fact_concurrency_stats FINAL and inherits the same
gap/crash risk. So every breakdown query here (traffic, geo, content
ranking) reads fact_concurrency_deltas directly instead: a plain count() of
delta_sessions=1 rows (session starts) per group, filtered to the window
first — no window function, no per-minute explosion, no dependency on
either fragile table. This reports session *volume* per group, not peak
concurrency per group; only the /concurrency and /kpis curve numbers (which
go through tools/concurrency.py's running-sum) are true concurrency.

All fact_concurrency_deltas reads here use FINAL — confirmed real duplicate
rows on this table (async ReplacingMergeTree dedup from
mv_compute_concurrency's REFRESH...APPEND reprocessing), ~13% overcounted
without it, and FINAL was faster in testing at current row counts. See
tools/concurrency.py's get_concurrency_curve docstring for the measurement."""
from fastapi import APIRouter

from . import ch_client
from .tools import concurrency

router = APIRouter(prefix="/api")


def _dims_from_query(platform=None, country=None, video_type=None, category=None,
                      content_id=None, video_resolution=None, show_name=None, title=None) -> dict:
    raw = {"platform": platform, "country": country, "video_type": video_type,
           "category": category, "content_id": content_id, "video_resolution": video_resolution,
           "show_name": show_name, "title": title}
    return {k: v for k, v in raw.items() if v is not None}


def _deltas_where(dims: dict, params: dict, start: str, end: str, only_starts: bool = True) -> str:
    """fact_concurrency_deltas has platform/country/video_resolution/content_id
    as direct columns; video_type/category need dictGet on content_id. Unlike
    tools/concurrency.py's running-sum queries, these are plain counts, so
    filtering the time window in this WHERE (not just on the output) is
    correct here — there's no cumulative window to reset."""
    params["start"] = start
    params["end"] = end
    clauses = ["minute >= {start:DateTime} AND minute < {end:DateTime}"]
    if only_starts:
        clauses.append("delta_sessions = 1")
    for col in ("platform", "country", "video_resolution"):
        if dims.get(col):
            clauses.append(f"{col} = {{{col}:String}}")
            params[col] = dims[col]
    for col in ("video_type", "category", "show_name", "title"):
        if dims.get(col):
            clauses.append(f"dictGet('dict_content', '{col}', content_id) = {{{col}:String}}")
            params[col] = dims[col]
    if dims.get("content_id"):
        clauses.append("content_id = {content_id:UInt64}")
        params["content_id"] = dims["content_id"]
    return " WHERE " + " AND ".join(clauses)


@router.get("/meta")
def meta():
    """Time bounds + distinct dimension values, for the filter dropdowns and
    the replay slider's min/max range."""
    bounds = ch_client.query("SELECT min(minute) AS min_ts, max(minute) AS max_ts FROM fact_concurrency_deltas")[0]
    dims = ch_client.query("""
        SELECT
            groupUniqArray(platform) AS platforms,
            groupUniqArray(country) AS countries
        FROM fact_concurrency_deltas
    """)[0]
    content_dims = ch_client.query("""
        SELECT groupUniqArray(video_type) AS video_types, groupUniqArray(category) AS categories,
               groupUniqArray(show_name) AS show_names
        FROM dim_content
    """)[0]
    # title is excluded here (12k+ distinct values, not dropdown-able) — the
    # UI takes it as free-text instead of a populated <select>.
    return {**bounds, **dims, **content_dims}


@router.get("/concurrency")
def concurrency_curve(start: str, end: str, grain: str = "minute",
                       platform: str | None = None, country: str | None = None,
                       video_type: str | None = None, category: str | None = None,
                       content_id: int | None = None, video_resolution: str | None = None,
                       show_name: str | None = None, title: str | None = None):
    """Powers both the live concurrency chart and the replay slider — replay
    is this same curve fetched once for the chosen window; the client
    animates through the points itself rather than the backend streaming."""
    dims = _dims_from_query(platform, country, video_type, category, content_id, video_resolution, show_name, title)
    curve = concurrency.get_concurrency_curve(dims, start, end, grain)
    # See /kpis — FINAL fixed the underlying dedup issue, this is now just a
    # defensive floor, not a workaround for a known-broken query.
    for row in curve:
        if row["concurrency"] < 0:
            row["concurrency"] = 0
    return curve


@router.get("/kpis")
def kpis(start: str, end: str, platform: str | None = None, country: str | None = None,
         video_type: str | None = None, category: str | None = None,
         content_id: int | None = None, video_resolution: str | None = None,
         show_name: str | None = None, title: str | None = None):
    dims = _dims_from_query(platform, country, video_type, category, content_id, video_resolution, show_name, title)
    # get_concurrency_curve now reads fact_concurrency_deltas FINAL, which
    # fixed the actual cause of transient negative concurrency (unmerged
    # ReplacingMergeTree duplicates). Clamp stays as a defensive floor, not
    # the fix — never let a KPI card show a negative viewer count.
    curve = concurrency.get_concurrency_curve(dims, start, end, "minute")
    peak = max((max(0, r["concurrency"]) for r in curve), default=0)
    avg = sum(max(0, r["concurrency"]) for r in curve) / len(curve) if curve else 0
    current = max(0, curve[-1]["concurrency"]) if curve else 0

    params: dict = {}
    where = _deltas_where(dims, params, start, end)
    users_row = ch_client.query(f"SELECT uniqExact(user_id) AS users FROM fact_concurrency_deltas FINAL {where}", params)
    distinct_users = users_row[0]["users"] if users_row else 0

    watch_params: dict = {"start": start, "end": end}
    watch_where = ["session_start >= {start:DateTime64(3,'UTC')} AND session_start < {end:DateTime64(3,'UTC')}"]
    # fact_events carries video_type/category/platform/country as direct
    # columns (unlike fact_concurrency_deltas/stats) — no dictGet needed here.
    for col in ("platform", "country", "video_type", "category", "show_name", "title"):
        if dims.get(col):
            watch_where.append(f"{col} = {{{col}:String}}")
            watch_params[col] = dims[col]
    if dims.get("content_id"):
        watch_where.append("content_id = {content_id:UInt64}")
        watch_params["content_id"] = dims["content_id"]
    avg_watch = ch_client.query(f"""
        SELECT avg(duration_sec) AS avg_watch_sec FROM (
            SELECT video_session_id, dateDiff('second', min(event_ts), max(event_ts)) AS duration_sec
            FROM fact_events
            WHERE {' AND '.join(watch_where)}
            GROUP BY video_session_id
        )
    """, watch_params)
    avg_watch_sec = avg_watch[0]["avg_watch_sec"] if avg_watch and avg_watch[0]["avg_watch_sec"] is not None else 0

    return {
        "peak_concurrency": peak,
        "avg_concurrency": round(avg, 1),
        "current_concurrency": current,
        "distinct_active_users": distinct_users,
        "avg_watch_seconds": round(avg_watch_sec, 1),
    }


_TIME_GRAIN_EXPR = {
    # hour/minute are "pattern across all days in the window" buckets (like
    # migrations-prod/007_queries.sql's per-platform-peak style), not a
    # timeline — that's what makes "hour of day" and "minute of day"
    # meaningful independent of how many days the window spans. "day" is the
    # one real-timeline grain: actual calendar dates in the window, so it
    # shows a trend over the range rather than a repeating pattern.
    "minute": "toHour(minute) * 60 + toMinute(minute)",  # 0..1439, minute-of-day
    "hour": "toHour(minute)",  # 0..23, hour-of-day
    "day": "toDate(minute)",  # actual calendar date
}
_SPLIT_DIMS = ("country", "platform", "video_resolution", "video_type", "category", "show_name", "title")
_MAX_SPLIT_SERIES = 8  # cap distinct series (category has ~80 opaque values); rest collapse into "Other"


def _split_expr(split_by: str) -> str:
    if split_by in ("video_type", "category", "show_name", "title"):
        return f"dictGet('dict_content', '{split_by}', content_id)"
    return split_by  # country / platform / video_resolution: direct columns


@router.get("/traffic")
def traffic(start: str, end: str, grain: str = "hour", split_by: str | None = None,
            platform: str | None = None, country: str | None = None,
            video_type: str | None = None, category: str | None = None,
            content_id: int | None = None, show_name: str | None = None, title: str | None = None):
    """Traffic chart: `grain` picks the time axis (minute-of-day, hour-of-day,
    or actual calendar day), `split_by` optionally breaks each bucket down by
    a dimension (country/platform/video_resolution/video_type/category/show_name/title) —
    the two combine into one query, e.g. "sessions per hour of day, split by
    platform". Without split_by, returns a single "value" series."""
    dims = _dims_from_query(platform, country, video_type, category, content_id, show_name=show_name, title=title)
    bucket_expr = _TIME_GRAIN_EXPR.get(grain, _TIME_GRAIN_EXPR["hour"])
    params: dict = {}
    where = _deltas_where(dims, params, start, end)

    if not split_by or split_by not in _SPLIT_DIMS:
        rows = ch_client.query(f"""
            SELECT {bucket_expr} AS bucket, count() AS value
            FROM fact_concurrency_deltas FINAL
            {where}
            GROUP BY bucket
            ORDER BY bucket
        """, params)
        return {"grain": grain, "split_by": None, "series": ["value"], "rows": rows}

    split_expr = _split_expr(split_by)
    raw = ch_client.query(f"""
        SELECT {bucket_expr} AS bucket, {split_expr} AS split_value, count() AS cnt
        FROM fact_concurrency_deltas FINAL
        {where}
        GROUP BY bucket, split_value
        ORDER BY bucket
    """, params)

    totals: dict = {}
    for r in raw:
        totals[r["split_value"]] = totals.get(r["split_value"], 0) + r["cnt"]
    top_series = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:_MAX_SPLIT_SERIES]]
    top_set = set(top_series)
    has_other = len(totals) > len(top_series)
    series = top_series + (["Other"] if has_other else [])

    by_bucket: dict = {}
    for r in raw:
        bucket = r["bucket"]
        key = r["split_value"] if r["split_value"] in top_set else "Other"
        row = by_bucket.setdefault(bucket, {"bucket": bucket})
        row[key] = row.get(key, 0) + r["cnt"]

    rows = [by_bucket[b] for b in sorted(by_bucket.keys())]
    for row in rows:
        for key in series:
            row.setdefault(key, 0)

    return {"grain": grain, "split_by": split_by, "series": series, "rows": rows}


@router.get("/geo")
def geo(start: str, end: str, platform: str | None = None,
        video_type: str | None = None, category: str | None = None,
        show_name: str | None = None, title: str | None = None):
    dims = _dims_from_query(platform, None, video_type, category, show_name=show_name, title=title)
    params: dict = {}
    where = _deltas_where(dims, params, start, end)
    return ch_client.query(f"""
        SELECT country, count() AS sessions, uniqExact(user_id) AS users
        FROM fact_concurrency_deltas FINAL
        {where}
        GROUP BY country
        ORDER BY sessions DESC
    """, params)


@router.get("/content")
def content_ranking(start: str, end: str, limit: int = 20,
                     platform: str | None = None, country: str | None = None,
                     video_type: str | None = None, category: str | None = None):
    dims = _dims_from_query(platform, country, video_type, category)
    params: dict = {"limit": limit}
    where = _deltas_where(dims, params, start, end)
    rows = ch_client.query(f"""
        SELECT content_id, count() AS sessions
        FROM fact_concurrency_deltas FINAL
        {where}
        GROUP BY content_id
        ORDER BY sessions DESC
        LIMIT {{limit:UInt32}}
    """, params)
    if not rows:
        return []
    ids = [r["content_id"] for r in rows]
    meta_rows = ch_client.query("""
        SELECT content_id, title, video_type, category, show_name
        FROM dim_content
        WHERE content_id IN {ids:Array(UInt64)}
        ORDER BY updated_at DESC
    """, {"ids": ids})
    meta_by_id = {}
    for m in meta_rows:
        meta_by_id.setdefault(m["content_id"], m)  # first hit wins = most recent (ordered by updated_at DESC)
    for r in rows:
        m = meta_by_id.get(r["content_id"], {})
        r["title"] = m.get("title", f"Content {r['content_id']}")
        r["video_type"] = m.get("video_type", "unknown")
        r["category"] = m.get("category", "unknown")
    return rows
