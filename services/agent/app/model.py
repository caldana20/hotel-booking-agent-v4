from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from services.agent.app.settings import SETTINGS


class ModelConfigError(RuntimeError):
    pass


def get_chat_model() -> Any:
    # LLM is always required. No deterministic fallback.
    if not SETTINGS.openai_api_key:
        raise ModelConfigError("OPENAI_API_KEY is not set. Configure it (infra/.env) and restart the agent.")

    return ChatOpenAI(
        model=SETTINGS.openai_model,
        api_key=SETTINGS.openai_api_key,
        base_url=SETTINGS.openai_base_url,
        temperature=0.2,
    )

