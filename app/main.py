"""FastAPI service wrapping the agent: live read-only mode, or the public demo's per-session sandbox."""

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.graph import build_graph
from app.config import get_settings
from app.logs import log_event, request_cost, token_counts
from app.mcp_client import ShopifyTools
from app.model_errors import model_unavailable
from app.rag.vectorstore import ChromaVectorStore
from app.release import release_manifest
from app.sandbox import Sandbox, SessionLimitError
from app.session import session_label

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

RESTING_MESSAGE = "The demo is resting right now. Please try again later."
SESSION_LIMIT_MESSAGE = "This demo chat has reached its limit. Start a new conversation to keep going."
TOO_LONG_MESSAGE = "This conversation is too long for the demo. Start a new conversation to keep going."
INTERNAL_MESSAGE = "Sorry, something went wrong on our side. Please try again in a moment."


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    session_state: str | None = Field(default=None, max_length=32000)


class ChatResponse(BaseModel):
    response: str
    intent: str
    latency_s: float
    tokens: int
    release: str
    sandbox: bool = False
    session_state: str | None = None
    session_reset: bool = False


def error_response(status: int, code: str, message: str, release: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, "release": release})


def create_app(graph: Any = None, tools: ShopifyTools | None = None, sandbox: Sandbox | None = None) -> FastAPI:
    settings = get_settings()
    release = release_manifest(settings)["release"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.tools = tools
        app.state.graph = graph
        app.state.sandbox = sandbox
        if graph is None and sandbox is None:
            store = ChromaVectorStore(settings.chroma_path)
            if settings.api_mode == "sandbox":
                client = AsyncAnthropic(api_key=settings.anthropic_api_key)
                app.state.sandbox = Sandbox(settings, store, client)
                await app.state.sandbox.start()
            else:
                live_tools = ShopifyTools()
                await live_tools.start()
                app.state.tools = live_tools
                app.state.graph = build_graph(settings, store, live_tools)
        try:
            yield
        finally:
            if app.state.tools is not None:
                await app.state.tools.aclose()

    app = FastAPI(title="Shopify Support Agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "release": release}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> Any:
        trace = uuid.uuid4().hex[:16]
        mode = "sandbox" if app.state.sandbox is not None else "live"
        log: dict[str, Any] = {"event": "chat", "trace": trace, "release": release, "mode": mode}
        if req.messages[-1].role != "user":
            return error_response(422, "bad_request", "The last message must be from the customer.", release)

        history = [m.model_dump() for m in req.messages][-settings.max_history_messages :]
        if sum(len(m["content"]) for m in history) > settings.max_conversation_chars:
            log_event(**log, status=413, error="conversation_too_long")
            return error_response(413, "conversation_too_long", TOO_LONG_MESSAGE, release)

        started = time.perf_counter()
        token, reset, writes, session = None, False, [], None
        try:
            if app.state.sandbox is not None:
                turn = await app.state.sandbox.turn(history, req.session_state)
                state, token, reset, writes, session = turn.state, turn.token, turn.reset, turn.writes, turn.session
            else:
                state = await app.state.graph.ainvoke({"messages": history})
        except SessionLimitError:
            log_event(**log, status=429, error="session_limit")
            return error_response(429, "session_limit", SESSION_LIMIT_MESSAGE, release)
        except Exception as e:
            unavailable = model_unavailable(e)
            log_event(**log, status=503 if unavailable else 500, error=type(e).__name__)
            if unavailable:
                return error_response(503, "model_unavailable", RESTING_MESSAGE, release)
            return error_response(500, "internal", INTERNAL_MESSAGE, release)
        latency = round(time.perf_counter() - started, 2)

        reply = state.get("response") or ""
        usage = state.get("usage", [])
        counts = token_counts(usage)
        intent = "injection" if state.get("hard_injection") else state.get("intent", "")
        log_event(
            **log,
            status=200 if reply else 500,
            session=session_label(session) if session else None,
            intent=intent,
            latency_ms=round(latency * 1000),
            nodes=state.get("timings", []),
            tokens=counts,
            cost_usd=request_cost(usage),
            writes=writes,
            pending=bool(state.get("pending_action")),
            session_reset=reset,
        )
        if not reply:
            return error_response(500, "internal", INTERNAL_MESSAGE, release)
        return ChatResponse(
            response=reply,
            intent=intent,
            latency_s=latency,
            tokens=counts["input_tokens"] + counts["output_tokens"],
            release=release,
            sandbox=mode == "sandbox",
            session_state=token,
            session_reset=reset,
        )

    if FRONTEND_DIR.is_dir():
        # mounted last so /health and /chat win, html=True serves index.html at /
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
