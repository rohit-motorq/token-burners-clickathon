"""LOOKUP + TREND genre tools. Reads cc_delta_content, whose ORDER BY is
(minute, content_id, platform, country, video_type, category) — time-range-
first, so every query here filters on minute first and treats dims as
secondary filters.

No narrow cc_delta_dims table exists under the migrationv2 schema (the
authoritative pipeline on rohitdevtesting, see INNER_CONTEXT.md) — every
query reads cc_delta_content directly, even without a content_id filter.
Still minute-range-bounded either way, just no separate narrow projection
for the no-content-filter case."""
from ..observability import observe
from .. import ch_client

_GRAIN_EXPR = {
    "minute": "minute",
    "hour": "toStartOfHour(minute)",
    "day": "toStartOfDay(minute)",
}

_DIM_COLUMNS = ("platform", "country", "video_type", "category")


def _dim_where_clause(dims: dict, params: dict) -> str:
    """Dimension filters only — never a time-range filter here. A time filter
    inside the running-sum's own subquery resets the cumulative sum to 0 at
    the window start, silently dropping whatever concurrency was already
    carried in from before it (see Docs/CONCURRENCY_VALIDATION.md Finding 1
    — this exact bug: computed=4 instead of the correct 54 at 10:00 for a
    10:00-11:00 window, because the running sum never saw the deltas from
    before 10:00). The time window must only ever be applied to the *output*
    of the cumulative sum, after it has run from the true beginning."""
    clauses = []
    for col in _DIM_COLUMNS:
        if dims.get(col):
            clauses.append(f"{col} = {{{col}:String}}")
            params[col] = dims[col]
    if dims.get("content_id"):
        clauses.append("content_id = {content_id:UInt64}")
        params["content_id"] = dims["content_id"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


@observe(as_type="tool")
def get_concurrency_curve(dims: dict, start: str, end: str, grain: str = "minute") -> list[dict]:
    """Minute/hour/day concurrency curve for a time range + dimension filter."""
    params: dict = {"start": start, "end": end}
    dim_where = _dim_where_clause(dims, params)
    bucket = _GRAIN_EXPR.get(grain, "minute")
    sql = f"""
        SELECT bucket, concurrency FROM (
            SELECT
                bucket,
                sum(step_delta) OVER (ORDER BY bucket) AS concurrency
            FROM (
                SELECT {bucket} AS bucket, sum(delta_sessions) AS step_delta
                FROM cc_delta_content
                {dim_where}
                GROUP BY bucket
            )
        )
        WHERE bucket >= {{start:DateTime}} AND bucket < {{end:DateTime}}
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
                    FROM cc_delta_content
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
