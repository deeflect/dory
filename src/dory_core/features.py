from __future__ import annotations

from dataclasses import asdict, dataclass

from dory_core.config import DorySettings


@dataclass(frozen=True, slots=True)
class SearchFeatures:
    bm25_enabled: bool = True
    vector_enabled: bool = True
    hybrid_enabled: bool = True
    session_recall_enabled: bool = True
    query_expansion_enabled: bool = False
    query_planner_enabled: bool = False
    reranker_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ActiveMemoryFeatures:
    enabled: bool = True
    include_wake_default: bool = True
    helper_wiki_enabled: bool = True
    session_context_policy: str = "profile"
    llm_planner_enabled: bool = False
    llm_composer_enabled: bool = False


@dataclass(frozen=True, slots=True)
class WriteFeatures:
    semantic_enabled: bool = True
    canonical_write_requires_allow_flag: bool = True
    proposals_enabled: bool = True
    quarantine_enabled: bool = True


@dataclass(frozen=True, slots=True)
class CompilerFeatures:
    dream_enabled: bool = True
    digest_enabled: bool = True
    maintenance_enabled: bool = True
    wiki_refresh_enabled: bool = True


@dataclass(frozen=True, slots=True)
class DoryFeatureFlags:
    search: SearchFeatures
    active_memory: ActiveMemoryFeatures
    write: WriteFeatures
    compiler: CompilerFeatures

    @classmethod
    def from_settings(cls, settings: DorySettings) -> DoryFeatureFlags:
        active_memory_llm_enabled = settings.active_memory_llm_provider != "off"
        return cls(
            search=SearchFeatures(
                query_expansion_enabled=settings.query_expansion_enabled and settings.query_expansion_max > 0,
                query_planner_enabled=settings.query_planner_enabled,
                reranker_enabled=settings.query_reranker_enabled,
            ),
            active_memory=ActiveMemoryFeatures(
                llm_planner_enabled=active_memory_llm_enabled
                and settings.active_memory_llm_stages in {"both", "plan"},
                llm_composer_enabled=active_memory_llm_enabled
                and settings.active_memory_llm_stages in {"both", "compose"},
            ),
            write=WriteFeatures(),
            compiler=CompilerFeatures(),
        )

    def as_status_payload(self) -> dict[str, object]:
        return asdict(self)
