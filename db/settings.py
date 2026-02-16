from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/hotel"
    default_tenant_id: str = "t_default"


SETTINGS = DbSettings()

