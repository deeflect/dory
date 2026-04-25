from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dory_core.active_memory import ActiveMemoryEngine
from dory_core.config import DorySettings
from dory_core.embedding import ContentEmbedder, build_runtime_embedder
from dory_core.llm.active_memory import build_active_memory_components
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.search import SearchEngine
from dory_core.wake import WakeBuilder


@dataclass(frozen=True, slots=True)
class SurfaceRuntime:
    corpus_root: Path
    index_root: Path
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None
    retrieval_planner: OpenRouterRetrievalPlanner | None
    reranker: Any
    rerank_candidate_limit: int
    search_engine: SearchEngine
    active_memory_engine: ActiveMemoryEngine


def build_surface_runtime(
    *,
    corpus_root: Path,
    index_root: Path,
    settings: DorySettings | None = None,
    embedder: ContentEmbedder | None = None,
    query_expander: OpenRouterQueryExpander | None = None,
    retrieval_planner: OpenRouterRetrievalPlanner | None = None,
    reranker: Any = None,
    rerank_candidate_limit: int | None = None,
) -> SurfaceRuntime:
    resolved_settings = settings or DorySettings()
    runtime_embedder = embedder or build_runtime_embedder()
    resolved_query_expander = query_expander if query_expander is not None else build_query_expander(resolved_settings)
    resolved_retrieval_planner = (
        retrieval_planner if retrieval_planner is not None else build_retrieval_planner(resolved_settings, purpose="query")
    )
    resolved_reranker = reranker if reranker is not None else build_reranker(resolved_settings)
    resolved_rerank_candidate_limit = (
        rerank_candidate_limit if rerank_candidate_limit is not None else resolved_settings.query_reranker_candidate_limit
    )
    search_engine = SearchEngine(
        Path(index_root),
        runtime_embedder,
        query_expander=resolved_query_expander,
        retrieval_planner=resolved_retrieval_planner,
        result_selector=resolved_retrieval_planner,
        reranker=resolved_reranker,
        rerank_candidate_limit=resolved_rerank_candidate_limit,
    )
    active_memory_planner, active_memory_composer = build_active_memory_components(resolved_settings)
    active_memory_engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(Path(corpus_root)),
        search_engine=search_engine,
        root=Path(corpus_root),
        planner=active_memory_planner,
        composer=active_memory_composer,
    )
    return SurfaceRuntime(
        corpus_root=Path(corpus_root),
        index_root=Path(index_root),
        embedder=runtime_embedder,
        query_expander=resolved_query_expander,
        retrieval_planner=resolved_retrieval_planner,
        reranker=resolved_reranker,
        rerank_candidate_limit=resolved_rerank_candidate_limit,
        search_engine=search_engine,
        active_memory_engine=active_memory_engine,
    )


def build_query_expander(settings: DorySettings) -> OpenRouterQueryExpander | None:
    if not settings.query_expansion_enabled or settings.query_expansion_max <= 0:
        return None
    client = build_openrouter_client(settings, purpose="query")
    if client is None:
        return None
    return OpenRouterQueryExpander(client=client, max_expansions=settings.query_expansion_max)


def build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    if purpose == "query" and not settings.query_planner_enabled:
        return None
    client = build_openrouter_client(settings, purpose=purpose)
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)
