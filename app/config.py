"""Central settings. Everything sensitive comes from .env, nothing secret lives in code."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    shopify_store_domain: str
    shopify_admin_token: str
    shopify_api_version: str = "2026-01"

    # empty is allowed so the mcp server can run without it, the agent
    # checks for a real key when it builds the anthropic client
    anthropic_api_key: str = ""

    # model choices, revisited after the eval phase
    router_model: str = "claude-haiku-4-5"
    answer_model: str = "claude-haiku-4-5"


@lru_cache
def get_settings() -> Settings:
    # cached so every import shares one instance
    return Settings()
