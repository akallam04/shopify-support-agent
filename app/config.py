"""Central settings. Everything sensitive comes from .env, nothing secret lives in code."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    shopify_store_domain: str = ""
    shopify_admin_token: str = ""
    shopify_write_token: str = ""
    shopify_api_version: str = "2026-10"

    store_backend: Literal["shopify", "sim"] = "shopify"
    write_actions: bool = False
    mutation_gate: bool = True
    gate_reflection: bool = True
    gate_confirmation: bool = True
    context_keep_messages: int = 8
    sim_seed_path: str = "data/sim/seed.json"
    sim_now: str = ""

    # empty is allowed so the mcp server can run without it, the agent
    # checks for a real key when it builds the anthropic client
    anthropic_api_key: str = ""

    # model choices, revisited after the eval phase
    router_model: str = "claude-haiku-4-5"
    answer_model: str = "claude-haiku-4-5"

    # judge stays a tier above whatever is being judged
    judge_model: str = "claude-sonnet-5"

    # comma-separated origins allowed to call /chat, "*" for local dev
    cors_origins: str = "*"
    # cap history the frontend can send, bounds cost and abuse
    max_history_messages: int = 20

    # vector index location, pointed at writable /tmp inside lambda
    chroma_path: str = "chroma_db"

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    # cached so every import shares one instance
    return Settings()
