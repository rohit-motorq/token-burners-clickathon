"""Minimal OpenAI-compatible /v1/chat/completions, enough for LibreChat's
'custom endpoint' integration. Run: uvicorn src.agent.server:app --port 8000"""
import json
import time
import uuid

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agent import answer
from . import chart_store
from .observability import get_client, enabled as _langfuse_enabled

app = FastAPI()


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
