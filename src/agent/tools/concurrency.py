"""LOOKUP + TREND genre tools. Reads fact_concurrency_deltas, whose ORDER BY
is (minute, video_session_id) — time-range-first, so every query here
filters on minute first and treats dims as secondary filters.

migrations-prod (the authoritative schema, see INNER_CONTEXT.md) keeps
fact_concurrency_deltas lean: platform/country/video_resolution are direct
columns, but video_type/category are content-derived and NOT stored on this
table — they're looked up via dictGet('dict_content', ..., content_id) at
query time, same dictionary get_content_metadata reads from.

No narrow cc_delta_dims table exists — every query reads
fact_concurrency_deltas directly, even without a content_id filter. Still
minute-range-bounded either way, just no separate narrow projection for the
no-content-filter case."""
from ..observability import observe
from .. import ch_client

_GRAIN_EXPR = {
    "minute": "minute",
    "hour": "toStartOfHour(minute)",
    "day": "toStartOfDay(minute)",
}

_DIM_COLUMNS = ("platform", "country", "video_resolution")
_DICT_DIM_COLUMNS = ("video_type", "category", "show_name", "title")


def _dim_where_clause(dims: dict, params: dict) -> str:
    """Dimension filters only — never a time-range filter here. A time filter
    inside the running-sum's own subquery resets the cumulative sum to 0 at
    the window start, silently dropping whatever concurrency was already
    carried in from before it (see Docs/CONCURRENCY_VALIDATION.md Finding 1
    — this exact bug: computed=4 instead of the correct 54 at 10:00 for a
    10:00-11:00 window, because the running sum never saw the deltas from
    before 10:00). The time window must only ever be applied to the *output*
    of the cumulative sum, after it has run from the true beginning.

    video_type/category aren't columns here — filtered via dictGet on
    content_id against dict_content instead."""
    clauses = []
    for col in _DIM_COLUMNS:
        if dims.get(col):
            clauses.append(f"{col} = {{{col}:String}}")
            params[col] = dims[col]
    for col in _DICT_DIM_COLUMNS:
        if dims.get(col):
            clauses.append(f"dictGet('dict_content', '{col}', content_id) = {{{col}:String}}")
            params[col] = dims[col]
    if dims.get("content_id"):
        clauses.append("content_id = {content_id:UInt64}")
        params["content_id"] = dims["content_id"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


@observe(as_type="tool")
def get_concurrency_curve(dims: dict, start: str, end: str, grain: str = "minute") -> list[dict]:
    """Minute/hour/day concurrency curve for a time range + dimension filter.

    FINAL on fact_concurrency_deltas: mv_compute_concurrency is a
    REFRESH...APPEND materialized view, and ReplacingMergeTree's background
    merge/dedup is async — without FINAL, a session recomputed more than
    once (dirty-session reprocessing) shows up as duplicate +1/-1 rows that
    haven't merged away yet, and sum(delta_sessions) double-counts them.
    Confirmed against rohitdevtestingv8: ~13% of rows are such unmerged
    duplicates (406,782 raw vs 352,238 after FINAL over the full history at
    time of writing). FINAL was also faster in that same test, so this
    isn't a tradeoff at current data volumes — revisit if the table grows
    large enough that FINAL's cost starts to dominate.

    ORDER BY minute WITH FILL zero-fills every minute with no delta activity,
    so the running sum is dense at minute resolution (not just the minutes a
    session started or ended in) before any downsampling happens. FROM/TO is
    bound to the same start/end params as the outer filter — safe because
    WITH FILL only adds synthetic gap rows inside that range, it never
    removes the real (unfiltered) rows before `start` that the running sum
    still needs to carry the correct cumulative value into the window (see
    _dim_where_clause docstring — same rule, WITH FILL doesn't change it).

    hour/day grain always computes the full minute-resolution curve first,
    then takes max(concurrency) per hour/day bucket — NOT concurrency at the
    bucket's start instant. That distinction matters a lot for bursty
    traffic: toStartOfDay(minute) as the running-sum's own grouping grain
    reports whatever the value happened to be exactly at midnight, which for
    a short live-content spike is usually ~0 even on a day that peaked at
    18,000+ mid-afternoon — confirmed this exact case against
    rohitdevtestingv8 (day-grain snapshot showed peak=2 for a month that
    genuinely peaked at 18,255). Peak-per-bucket is the correct downsample
    for a concurrency chart; a snapshot-per-bucket silently aliases away
    every spike that isn't still running at the bucket boundary."""
    params: dict = {"start": start, "end": end}
    dim_where = _dim_where_clause(dims, params)
    bucket = _GRAIN_EXPR.get(grain, "minute")
    sql = f"""
        SELECT bucket, max(concurrency) AS concurrency FROM (
            SELECT
                {bucket} AS bucket,
                minute,
                sum(step_delta) OVER (ORDER BY minute) AS concurrency
            FROM (
                SELECT minute, sum(delta_sessions) AS step_delta
                FROM fact_concurrency_deltas FINAL
                {dim_where}
                GROUP BY minute
                ORDER BY minute WITH FILL
                    FROM {{start:DateTime}}
                    TO {{end:DateTime}}
                    STEP INTERVAL 1 MINUTE
            )
        )
        WHERE minute >= {{start:DateTime}} AND minute < {{end:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
    """
    return ch_client.query(sql, params)


@observe(as_type="tool")
def get_peak(dims: dict, start: str, end: str, grain: str = "minute") -> dict:
    """Peak concurrency + the minute it occurred. Never sum peaks across
    disjoint slices — this always computes the curve for the exact filter
    first, then takes max() over it (LLD §12.1)."""
    curve = get_concurrency_curve(dims, start, end, grain)
    if not curve:
        return {"peak_value": 0, "peak_bucket": None}
    peak_row = max(curve, key=lambda r: r["concurrency"])
    return {"peak_value": peak_row["concurrency"], "peak_bucket": peak_row["bucket"]}


@observe(as_type="tool")
def get_active_users(content_id: int, at_minute: str) -> dict:
    """Distinct users still active at `at_minute` for a content_id: the
    session's open/close events (delta_sessions +1/-1) net to > 0 as of
    that minute. Same no-time-filter-inside-the-cumulative-sum rule as
    get_peak/get_concurrency_curve — this reads full history up to
    at_minute, not just a window, or open sessions started earlier get
    silently excluded.

    ponytail: approximates "active at minute X" via video_session_id's net
    delta_sessions up to that minute, not a true point-in-time snapshot
    join — good enough for "who was around at the peak", revisit if a
    session-range table ever exists."""
    sql = """
        SELECT uniqExact(user_id) AS active_users, count() AS active_sessions
        FROM (
            SELECT video_session_id, argMax(user_id, minute) AS user_id,
                   sum(delta_sessions) AS net
            FROM fact_concurrency_deltas FINAL
            WHERE content_id = {content_id:UInt64} AND minute <= {at_minute:DateTime}
            GROUP BY video_session_id
            HAVING net > 0
        )
    """
    rows = ch_client.query(sql, {"content_id": content_id, "at_minute": at_minute})
    row = rows[0] if rows else {"active_users": 0, "active_sessions": 0}
    return {"content_id": content_id, "at_minute": at_minute, **row}


@observe(as_type="tool")
def get_trend(dims: dict, end: str, lookback_minutes: int = 10) -> dict:
    """Rate of change over the last N minute-buckets. Delta/slope computed in
    SQL, not left for the LLM to eyeball from a list of numbers."""
    params: dict = {"end": end, "lookback_minutes": lookback_minutes}
    dim_where = _dim_where_clause(dims, params)
    sql = f"""
        SELECT minute, cc, delta, delta / nullIf(cc - delta, 0) AS pct_change
        FROM (
            -- delta must be computed here, over the FULL unfiltered history —
            -- SQL applies WHERE before window functions in the same SELECT,
            -- so filtering the lookback window at this level (instead of one
            -- level further out) would cut lagInFrame's visibility into
            -- whatever row came right before the window, making the oldest
            -- row in the window always show delta == cc (as if from zero).
            SELECT
                minute,
                cc,
                cc - lagInFrame(cc, 1) OVER (ORDER BY minute) AS delta
            FROM (
                SELECT minute, sum(step_delta) OVER (ORDER BY minute) AS cc
                FROM (
                    SELECT minute, sum(delta_sessions) AS step_delta
                    FROM fact_concurrency_deltas FINAL
                    {dim_where}
                    GROUP BY minute
                )
            )
        )
        WHERE minute >= {{end:DateTime}} - INTERVAL {{lookback_minutes:UInt32}} MINUTE
          AND minute <= {{end:DateTime}}
        ORDER BY minute DESC
    """
    rows = ch_client.query(sql, params)
    if len(rows) < 2:
        return {"points": rows, "delta_pct": None, "slope_per_min": None, "direction": "insufficient_data"}
    latest, prev = rows[0], rows[1]
    delta_pct = latest["pct_change"]
    direction = "rising" if (latest["delta"] or 0) > 0 else "falling" if (latest["delta"] or 0) < 0 else "flat"
    return {
        "points": rows,
        "delta_pct": delta_pct,
        "slope_per_min": latest["delta"],
        "direction": direction,
    }
