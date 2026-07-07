"""Terminal chat with the agent, the end-to-end rig until the web frontend exists.

Run from the repo root: .venv/bin/python -m scripts.chat_repl
"""

import asyncio
import time
from typing import Any

from app.agent.graph import build_graph
from app.config import get_settings
from app.mcp_client import ShopifyTools
from app.rag.vectorstore import ChromaVectorStore


async def main() -> None:
    settings = get_settings()
    tools = ShopifyTools()
    await tools.start()
    graph = build_graph(settings, ChromaVectorStore(), tools)
    print("aurora outfitters support agent, type 'quit' to exit")

    history: list[dict[str, Any]] = []
    try:
        while True:
            try:
                user = input("\nyou: ").strip()
            except EOFError:
                break
            if not user or user.lower() in {"quit", "exit"}:
                break

            started = time.perf_counter()
            state = await graph.ainvoke(
                {"messages": history + [{"role": "user", "content": user}]}
            )
            elapsed = time.perf_counter() - started

            reply = state["response"]
            print(f"agent: {reply}")
            tokens = sum(
                u["input_tokens"] + u["output_tokens"] for u in state.get("usage", [])
            )
            print(
                f"[intent {state.get('intent', 'hard-injection')} | {elapsed:.1f}s | "
                f"{tokens} tokens | retries {state.get('retry_count', 0)}]"
            )
            history.append({"role": "user", "content": user})
            history.append({"role": "assistant", "content": reply})
    finally:
        await tools.aclose()


if __name__ == "__main__":
    asyncio.run(main())
