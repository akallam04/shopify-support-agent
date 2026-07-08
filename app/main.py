"""FastAPI service wrapping the agent. Owns one MCP session for the app's lifetime."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.graph import build_graph
from app.config import get_settings
from app.mcp_client import ShopifyTools
from app.rag.vectorstore import ChromaVectorStore

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class ChatResponse(BaseModel):
    response: str
    intent: str
    latency_s: float
    tokens: int


def create_app(graph: Any = None, tools: ShopifyTools | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # a preset graph means a test injected one, skip the real mcp startup
        if graph is None:
            live_tools = ShopifyTools()
            await live_tools.start()
            app.state.tools = live_tools
            app.state.graph = build_graph(settings, ChromaVectorStore(), live_tools)
        else:
            app.state.tools = tools
            app.state.graph = graph
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
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        if req.messages[-1].role != "user":
            raise HTTPException(422, "the last message must be from the user")

        import time

        history = [m.model_dump() for m in req.messages][-settings.max_history_messages :]
        started = time.perf_counter()
        state = await app.state.graph.ainvoke({"messages": history})
        latency = round(time.perf_counter() - started, 2)

        reply = state.get("response") or ""
        if not reply:
            raise HTTPException(502, "the agent did not produce a response")
        intent = "injection" if state.get("hard_injection") else state.get("intent", "")
        tokens = sum(
            u["input_tokens"] + u["output_tokens"] for u in state.get("usage", [])
        )
        return ChatResponse(response=reply, intent=intent, latency_s=latency, tokens=tokens)

    if FRONTEND_DIR.is_dir():
        # mounted last so /health and /chat win, html=True serves index.html at /
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
