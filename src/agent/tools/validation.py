"""SA <= SI invariant check (LLD §8.4/§12.6) — distinguishes a real
system/pipeline issue from an organic concurrency drop.

NOT AVAILABLE under the migrationv2 schema (rohitdevtesting, the current
CH_DATABASE default): that pipeline tracks only a single is_active state,
no separate session-independent presence sketch (cc_delta_dims and
cc_si_minute both don't exist there). Deliberately NOT registered in
agent.py's GENRE_TOOLS/TOOL_SCHEMAS or mcp_server's tool list — the LLM must
never be offered a tool that will hard-fail against the live schema. Kept
here, unused, in case a target DB with those tables (see src/migrations/)
is ever configured again."""
from ..observability import observe
from .. import ch_client


@observe(as_type="tool")
def get_si_sa_gap(dims: dict, start: str, end: str) -> list[dict]:
    dim_clauses = []
    params = {"start": start, "end": end}
    for col in ("platform", "country", "video_type"):
        if dims.get(col):
            dim_clauses.append(f"{col} = {{{col}:String}}")
            params[col] = dims[col]
    dim_where = (" AND " + " AND ".join(dim_clauses)) if dim_clauses else ""
    sql = f"""
        WITH sa AS (
            SELECT minute, sum(step_delta) OVER (ORDER BY minute) AS cc
            FROM (
                SELECT minute, sum(delta_sessions) AS step_delta
                FROM cc_delta_dims
                WHERE minute >= {{start:DateTime}} AND minute < {{end:DateTime}}{dim_where}
                GROUP BY minute
            )
        ),
        si AS (
            SELECT minute, uniqCombinedMerge(sessions_state) AS cc
            FROM cc_si_minute
            WHERE minute >= {{start:DateTime}} AND minute < {{end:DateTime}}{dim_where}
            GROUP BY minute
        )
        SELECT sa.minute AS minute, sa.cc AS sa_count, si.cc AS si_count,
               si.cc - sa.cc AS phantom_audience
        FROM sa LEFT JOIN si ON sa.minute = si.minute
        ORDER BY sa.minute
    """
    return ch_client.query(sql, params)
