"""Settings for the simulation harness, kept apart from the app's own settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SimSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    sim_user_base_url: str = ""
    sim_user_api_key: str = ""
    sim_user_model: str = ""
    sim_user_temperature: float = 0.7
    sim_judge_model: str = "claude-sonnet-5-5"
    max_agent_turns: int = 12
    concurrency: int = 4


@lru_cache
def get_sim_settings() -> SimSettings:
    return SimSettings()
