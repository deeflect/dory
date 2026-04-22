from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dory_core.config import DorySettings
from dory_core.llm.json_client import JSONGenerationClient
from dory_core.llm.openai_compatible import build_local_llm_client
from dory_core.llm.openrouter import OpenRouterConfigurationError, build_openrouter_client

DreamBackend = Literal["local", "openrouter"]


@dataclass(frozen=True, slots=True)
class DreamLLM:
    client: JSONGenerationClient
    backend: DreamBackend


def build_dream_llm(settings: DorySettings | None = None) -> DreamLLM | None:
    resolved_settings = settings or DorySettings()
    provider = resolved_settings.dream_llm_provider
    if provider == "local":
        local_client = build_local_llm_client(resolved_settings)
        return DreamLLM(client=local_client, backend="local") if local_client is not None else None
    if provider == "openrouter":
        openrouter_client = build_openrouter_client(resolved_settings, purpose="dream")
        return DreamLLM(client=openrouter_client, backend="openrouter") if openrouter_client is not None else None
    if provider == "auto":
        local_client = build_local_llm_client(resolved_settings)
        if local_client is not None:
            return DreamLLM(client=local_client, backend="local")
        openrouter_client = build_openrouter_client(resolved_settings, purpose="dream")
        return DreamLLM(client=openrouter_client, backend="openrouter") if openrouter_client is not None else None
    raise ValueError(f"Unsupported dream LLM provider: {provider}")


def build_dream_llm_client(settings: DorySettings | None = None) -> JSONGenerationClient | None:
    dream_llm = build_dream_llm(settings)
    return dream_llm.client if dream_llm is not None else None


def require_dream_llm(settings: DorySettings | None = None) -> DreamLLM:
    dream_llm = build_dream_llm(settings)
    if dream_llm is not None:
        return dream_llm
    resolved_settings = settings or DorySettings()
    if resolved_settings.dream_llm_provider == "local":
        raise OpenRouterConfigurationError(
            "Local dream LLM is not configured. Set DORY_LOCAL_LLM_BASE_URL, "
            "DORY_LOCAL_LLM_MODEL, and DORY_LOCAL_LLM_API_KEY."
        )
    if resolved_settings.dream_llm_provider == "auto":
        raise OpenRouterConfigurationError(
            "No dream LLM is configured. Set local DORY_LOCAL_LLM_* values or "
            "DORY_OPENROUTER_API_KEY."
        )
    raise OpenRouterConfigurationError(
        "OpenRouter API key is missing. Set DORY_OPENROUTER_API_KEY or OPENROUTER_API_KEY, "
        "or set DORY_DREAM_LLM_PROVIDER=local."
    )
