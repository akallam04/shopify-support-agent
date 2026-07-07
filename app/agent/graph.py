"""Wires the nodes into the state machine. The whole control flow lives on this page."""

from anthropic import AsyncAnthropic
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.order_tools import make_order_tools_node
from app.agent.nodes.respond import make_respond_node
from app.agent.nodes.retrieve import make_retrieve_node
from app.agent.nodes.route import make_route_node
from app.agent.nodes.sanitize import sanitize_node
from app.agent.nodes.verify import verify_node
from app.agent.state import AgentState
from app.config import Settings
from app.mcp_client import ShopifyTools
from app.rag.vectorstore import VectorStore


def _after_sanitize(state: AgentState) -> str:
    return "respond" if state.get("hard_injection") else "route"


def _after_route(state: AgentState) -> str:
    intent = state["intent"]
    if intent in ("product", "policy"):
        return "retrieve"
    if intent == "order":
        return "order_tools"
    return "respond"


def _after_verify(state: AgentState) -> str:
    return END if state.get("response") else "respond"


def build_graph(settings: Settings, store: VectorStore, tools: ShopifyTools):
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set, fill it in .env")
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    g = StateGraph(AgentState)
    g.add_node("sanitize", sanitize_node)
    g.add_node("route", make_route_node(client, settings.router_model))
    g.add_node("retrieve", make_retrieve_node(store))
    g.add_node("order_tools", make_order_tools_node(client, settings.answer_model, tools))
    g.add_node("respond", make_respond_node(client, settings.answer_model))
    g.add_node("verify", verify_node)

    g.add_edge(START, "sanitize")
    g.add_conditional_edges("sanitize", _after_sanitize)
    g.add_conditional_edges("route", _after_route)
    g.add_edge("retrieve", "respond")
    g.add_edge("order_tools", "verify")
    g.add_edge("respond", "verify")
    g.add_conditional_edges("verify", _after_verify)

    return g.compile()
