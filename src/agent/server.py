"""Minimal OpenAI-compatible /v1/chat/completions, enough for LibreChat's
'custom endpoint' integration. Run: uvicorn src.agent.server:app --port 8000"""
import json
import time
import uuid

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agent import answer, _reference_now
from . import chart_store
from .tools import concurrency, dashboard
from .observability import get_client, enabled as _langfuse_enabled
from .dashboard_api import router as dashboard_api_router

app = FastAPI()
app.include_router(dashboard_api_router)


def _sample_dashboard_reply() -> str:
    """LLM-free path (question == "TEST_DASHBOARD") — builds render_dashboard's
    real HTML from real ClickHouse data, no Anthropic credits needed. Useful
    for verifying the artifact pipeline end to end while credits are out."""
    from datetime import datetime, timedelta
    ref_now = _reference_now() or "2026-07-26 11:30:00"
    end_dt = datetime.strptime(ref_now, "%Y-%m-%d %H:%M:%S")
    start_dt = end_dt - timedelta(hours=1)
    curve = concurrency.get_concurrency_curve({"platform": "ANDROID_PHONE"},
                                               start_dt.strftime("%Y-%m-%d %H:%M:%S"), ref_now, "minute")
    peak = concurrency.get_peak({"platform": "ANDROID_PHONE"},
                                 start_dt.strftime("%Y-%m-%d %H:%M:%S"), ref_now, "minute")
    avg = sum(r["concurrency"] for r in curve) / len(curve) if curve else 0
    rising = curve[-1]["concurrency"] >= curve[0]["concurrency"] if len(curve) >= 2 else True
    entries = [
        {"kind": "stat", "label": "Peak concurrency", "value": f"{peak['peak_value']:,}",
         "sub": f"at {peak['peak_bucket']}"},
        {"kind": "stat", "label": "Average concurrency", "value": f"{avg:,.0f}",
         "sub": "over the last hour"},
        {"kind": "stat", "label": "Current trend", "value": "Rising" if rising else "Falling",
         "accent": "#1dd1a1" if rising else "#ff6b6b"},
        {"kind": "stat", "label": "Platform", "value": "Android phones", "accent": "#c8d6e5"},
        {"kind": "chart", "series": curve, "x_key": "bucket", "y_key": "concurrency",
         "chart_title": "ANDROID_PHONE concurrency, last hour"},
    ]
    return "Here is a sample dashboard, built from live data with no LLM call.\n\n" + \
        dashboard.render_dashboard_html("Sample Dashboard", entries,
                                         subtitle=f"ANDROID_PHONE · last hour · as of {ref_now}")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "concurrency-agent"
    messages: list[Message]
    user: str | None = None  # OpenAI schema's end-user id, forwarded as Langfuse user_id
    stream: bool = False


def _sse_stream(reply: str, chunk_id: str, model: str):
    """agent.answer() isn't itself a token-streaming generator — it computes
    the whole reply first. LibreChat's client, however, requests stream=true
    by default and its SSE parser silently shows nothing if the response
    isn't SSE-shaped at all (confirmed: our non-streaming JSON was reaching
    it as 200 OK with a real, correct reply — LibreChat just couldn't render
    it). This fakes streaming as a single content delta, which is enough for
    the client to render the full answer."""
    created = int(time.time())
    base = {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model}

    def chunk(delta: dict, finish_reason=None):
        return "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}) + "\n\n"

    yield chunk({"role": "assistant"})
    yield chunk({"content": reply})
    yield chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    question = req.messages[-1].content
    if question.strip() == "TEST_DASHBOARD":
        # LLM-free smoke test for the artifact pipeline — no Anthropic call,
        # real ClickHouse data. See _sample_dashboard_reply above.
        reply = _sample_dashboard_reply()
    else:
        reply = answer(question, user_id=req.user)
        # short-lived request in a low-traffic hackathon demo — flush so the
        # trace is visible in Langfuse immediately rather than waiting on the
        # background batch interval.
        if _langfuse_enabled:
            get_client().flush()

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if req.stream:
        return StreamingResponse(_sse_stream(reply, chunk_id, req.model), media_type="text/event-stream")

    return {
        "id": chunk_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply},
            "finish_reason": "stop",
        }],
    }


@app.get("/charts/{chart_id}.png")
def get_chart(chart_id: str):
    png = chart_store.get(chart_id)
    if png is None:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png")


@app.get("/health")
def health():
    return {"status": "ok"}
