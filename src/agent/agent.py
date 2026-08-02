"""Tool-calling loop. Router picks a genre -> restricted tool subset + system
prompt; the LLM only ever calls these typed tools, never raw SQL. Bounded to
MAX_TURNS tool round-trips per question."""
import json

from anthropic import Anthropic

from . import config, ch_client, chart_store
from .router import classify
from .prompts import system_prompt_for
from .observability import observe, get_client, propagate_attributes, enabled as _langfuse_enabled
from .tools import concurrency, content, health, billing, chart

MAX_TURNS = 5
_client = Anthropic(api_key=config.ANTHROPIC_API_KEY or None)


def _reference_now() -> str | None:
    """Latest event_ts in the data, not wall-clock time — this is a
    replayed/synthetic dataset (see PROBLEM_STATEMENT.md), so "now"/"last
    hour"/"yesterday" in a question must resolve against where the data
    actually is, not the real calendar date. Without this the model guesses
    a plausible-looking year and every relative-time query silently returns
    nothing. Not a tool (no LLM/user input involved) — fixed bookkeeping
    query feeding the system prompt, fine to call ch_client directly here.

    event_ts is DateTime64(3) — carries milliseconds. Every tool's start/end
    params are bound as plain {..:DateTime}, which fails to parse a fractional-
    second string (ClickHouse 500s on it). Truncate here rather than widen
    every tool's param type, since this is the only source of sub-second
    timestamps the model ever sees."""
    rows = ch_client.query("SELECT max(event_ts) AS mx FROM events_raw")
    mx = rows[0]["mx"] if rows and rows[0].get("mx") else None
    return mx.split(".")[0] if mx else None

_DIM_PARAMS = {
    "platform": {"type": "string"},
    "country": {"type": "string"},
    "video_type": {"type": "string"},
    "category": {"type": "string"},
    "content_id": {"type": "integer"},
}

TOOL_SCHEMAS = {
    "get_concurrency_curve": {
        "description": "Concurrency curve for a time range + optional dimension filter.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_DIM_PARAMS,
                "start": {"type": "string", "description": "ISO datetime, inclusive"},
                "end": {"type": "string", "description": "ISO datetime, exclusive"},
                "grain": {"type": "string", "enum": ["minute", "hour", "day"], "default": "minute"},
            },
            "required": ["start", "end"],
        },
    },
    "get_peak": {
        "description": "Peak concurrency + the bucket it occurred in, for a time range + filter.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_DIM_PARAMS,
                "start": {"type": "string"},
                "end": {"type": "string"},
                "grain": {"type": "string", "enum": ["minute", "hour", "day"], "default": "minute"},
            },
            "required": ["start", "end"],
        },
    },
    "get_trend": {
        "description": "Rate of change over the last N minute-buckets ending at `end`.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_DIM_PARAMS,
                "end": {"type": "string"},
                "lookback_minutes": {"type": "integer", "default": 10},
            },
            "required": ["end"],
        },
    },
    "get_content_metadata": {
        "description": "Title/video_type/category/scheduled_end_ts for a content_id.",
        "input_schema": {
            "type": "object",
            "properties": {"content_id": {"type": "integer"}},
            "required": ["content_id"],
        },
    },
    "get_health_signals": {
        "description": "Error/buffer event rate for a content_id over a time window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content_id": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["content_id", "start", "end"],
        },
    },
    # get_si_sa_gap deliberately not registered here — cc_delta_dims/cc_si_minute
    # don't exist under the migrationv2 schema (rohitdevtesting). See
    # src/agent/tools/validation.py and INNER_CONTEXT.md.
    "get_billable_impressions": {
        "description": "Estimated billable impressions for an advertiser over a time range. Not authoritative for invoicing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "advertiser_id": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["advertiser_id", "start", "end"],
        },
    },
    "render_chart": {
        # No "series" param — deliberately. Requiring the model to copy the
        # full data array as a literal tool-call argument means regenerating
        # potentially hundreds of rows of JSON inside its own output, which
        # burns through max_tokens before it can finish (confirmed: this
        # produced empty tool_use blocks with stop_reason=max_tokens and no
        # text, which the loop then returned as an empty final answer). The
        # dispatch layer injects the most recent curve/trend result instead —
        # the model only needs to say "render a chart," not reproduce data
        # it already saw.
        "description": "Render the most recently fetched concurrency curve/trend as a chart image, attached automatically to your reply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "chart_type": {"type": "string", "enum": ["line", "bar"], "default": "line"},
            },
            "required": [],
        },
    },
}

GENRE_TOOLS = {
    "LOOKUP": ["get_concurrency_curve", "get_peak", "render_chart"],
    "TREND": ["get_trend", "render_chart"],
    "BILLING": ["get_billable_impressions"],
    "DIAGNOSTIC": ["get_concurrency_curve", "get_content_metadata", "get_health_signals",
                   "render_chart"],
}


def _dims_from(kwargs: dict) -> dict:
    return {k: kwargs[k] for k in _DIM_PARAMS if kwargs.get(k) is not None}


def _dispatch(name: str, kwargs: dict, chart_context: dict | None = None):
    """chart_context, if given, is mutated with the most recent chartable
    result (get_concurrency_curve/get_trend) and read back by render_chart —
    see the comment on TOOL_SCHEMAS["render_chart"] for why the model never
    supplies the series data itself."""
    if name == "get_concurrency_curve":
        result = concurrency.get_concurrency_curve(_dims_from(kwargs), kwargs["start"], kwargs["end"],
                                                     kwargs.get("grain", "minute"))
        if chart_context is not None and result:
            chart_context.update(series=result, x_key="bucket", y_key="concurrency")
        return result
    if name == "get_peak":
        return concurrency.get_peak(_dims_from(kwargs), kwargs["start"], kwargs["end"],
                                     kwargs.get("grain", "minute"))
    if name == "get_trend":
        result = concurrency.get_trend(_dims_from(kwargs), kwargs["end"], kwargs.get("lookback_minutes", 10))
        if chart_context is not None and result.get("points"):
            chart_context.update(series=result["points"], x_key="minute", y_key="cc")
        return result
    if name == "get_content_metadata":
        return content.get_content_metadata(kwargs["content_id"])
    if name == "get_health_signals":
        return health.get_health_signals(kwargs["content_id"], kwargs["start"], kwargs["end"])
    if name == "get_billable_impressions":
        return billing.get_billable_impressions(kwargs["advertiser_id"], kwargs["start"], kwargs["end"])
    if name == "render_chart":
        series = (chart_context or {}).get("series")
        if not series:
            return {"error": "No concurrency curve/trend data fetched yet — "
                              "call get_concurrency_curve or get_trend before render_chart."}
        # Served over HTTP, not embedded as a data: URI — chat UIs (confirmed:
        # LibreChat's react-markdown) strip data: URIs from image src by
        # default for security; only http(s) URLs render. See chart_store.py.
        png_bytes = chart.render_chart_png(series, kwargs.get("title", ""),
                                            chart_context.get("x_key", "minute"),
                                            chart_context.get("y_key", "concurrency"),
                                            kwargs.get("chart_type", "line"))
        chart_id = chart_store.put(png_bytes)
        title = kwargs.get("title") or "Chart"
        return f"![{title}]({config.AGENT_PUBLIC_BASE_URL}/charts/{chart_id}.png)"
    raise ValueError(f"unknown tool: {name}")


@observe(as_type="generation")
def _call_model(system: str, messages: list, tools: list):
    """Isolated so the raw Anthropic call is its own generation-type
    observation — model/input/output/token usage attached to it directly,
    not folded into the parent span as an opaque function call."""
    resp = _client.messages.create(
        model=config.AGENT_MODEL,
        system=system,
        messages=messages,
        tools=tools,
        max_tokens=1024,
    )
    if _langfuse_enabled:
        get_client().update_current_generation(
            model=config.AGENT_MODEL,
            input=messages,
            output=[b.model_dump() if hasattr(b, "model_dump") else str(b) for b in resp.content],
            usage_details={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
        )
    return resp


@observe(name="concurrency-agent-query")
def answer(question: str, _trace: list | None = None,
           user_id: str | None = None, session_id: str | None = None) -> str:
    """_trace, if given, gets appended with each tool name called, in order —
    lets the eval harness check tool-call sequence without depending on
    Langfuse being configured (Langfuse tracing happens regardless, via the
    @observe() decorators; this is a second, always-available channel).

    user_id/session_id, if given (e.g. from LibreChat's request), get
    propagated to every span in this trace — lets Langfuse group/filter by
    conversation or end user, not just by individual question."""
    genre = classify(question)

    with propagate_attributes(tags=[genre], metadata={"question": question},
                               user_id=user_id, session_id=session_id):
        tool_names = GENRE_TOOLS[genre]
        tools = [{"name": n, **TOOL_SCHEMAS[n]} for n in tool_names]

        messages = [{"role": "user", "content": question}]
        system = system_prompt_for(genre)
        ref_now = _reference_now()
        if ref_now:
            system += (
                f"\n\nCurrent reference time: {ref_now}. This is a replayed/synthetic "
                "dataset — resolve \"now\"/\"right now\"/\"last hour\"/\"yesterday\"/"
                "\"last N minutes\" relative to THIS timestamp, never the real-world "
                "calendar date."
            )

        # render_chart's actual base64 markdown is attached here, out of band —
        # never sent back to the model as a tool_result to copy. A ~40-80KB
        # base64 PNG is tens of thousands of characters; asking the model to
        # reproduce that verbatim within max_tokens=1024 just truncates it
        # mid-string into garbage. The model only sees a short confirmation.
        chart_images: list[str] = []
        chart_context: dict = {}

        for _ in range(MAX_TURNS):
            resp = _call_model(system, messages, tools)
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text")
                if chart_images:
                    text += "\n\n" + "\n\n".join(chart_images)
                return text

            tool_results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                if _trace is not None:
                    _trace.append(block.name)
                try:
                    result = _dispatch(block.name, block.input, chart_context)
                except Exception as e:
                    result = {"error": str(e)}
                if block.name == "render_chart" and isinstance(result, str):
                    chart_images.append(result)
                    model_facing_result = {"status": "chart rendered and will be attached to the final reply automatically — do not attempt to reproduce the image data yourself"}
                else:
                    model_facing_result = result
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(model_facing_result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})

        text = "Reached max tool-call turns without a final answer."
        if chart_images:
            text += "\n\n" + "\n\n".join(chart_images)
        return text
