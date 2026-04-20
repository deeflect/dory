from __future__ import annotations

from dory_core.config import DorySettings
from dory_core.llm.openai_compatible import build_local_llm_client
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner


def build_active_memory_planner(settings: DorySettings | None = None) -> OpenRouterRetrievalPlanner | None:
    planner, _composer = build_active_memory_components(settings)
    return planner


def build_active_memory_components(
    settings: DorySettings | None = None,
) -> tuple[OpenRouterRetrievalPlanner | None, OpenRouterRetrievalPlanner | None]:
    resolved_settings = settings or DorySettings()
    planner = _build_active_memory_llm(resolved_settings)
    if planner is None:
        return None, None
    stages = resolved_settings.active_memory_llm_stages
    return (
        planner if stages in {"both", "plan"} else None,
        planner if stages in {"both", "compose"} else None,
    )


def _build_active_memory_llm(resolved_settings: DorySettings) -> OpenRouterRetrievalPlanner | None:
    provider = resolved_settings.active_memory_llm_provider
    if provider == "off":
        return None

    if provider in {"local", "auto"}:
        local_client = build_local_llm_client(resolved_settings)
        if local_client is not None:
            return OpenRouterRetrievalPlanner(client=local_client)
        if provider == "local":
            return None

    client = build_openrouter_client(resolved_settings, purpose="maintenance")
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)
