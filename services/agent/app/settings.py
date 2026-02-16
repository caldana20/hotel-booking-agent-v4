from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    database_url: str
    default_tenant_id: str = "t_default"
    tools_base_url: str = "http://localhost:8001"

    # Model (OpenAI-compatible)
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"

    log_level: str = "info"
    admin_token: str = "dev-admin"

    # Guardrails
    max_tool_calls_per_turn: int = 8
    max_hotels_priced_per_turn: int = 20
    max_wall_clock_ms: int = 8000

    tool_timeout_ms: int = 2500
    tool_max_retries: int = 2


SETTINGS = AgentSettings()

