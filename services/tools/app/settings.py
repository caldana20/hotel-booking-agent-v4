from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolsSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    database_url: str
    default_tenant_id: str = "t_default"
    log_level: str = "info"

    max_hotel_ids_per_request: int = 50
    max_candidates: int = 50
    max_offers: int = 500


SETTINGS = ToolsSettings()

