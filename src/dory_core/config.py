from __future__ import annotations

from dataclasses import dataclass
from os import cpu_count
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DorySettings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DORY_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    root: str = "."
    corpus_root: str | None = None
    index_root: str | None = None
    auth_tokens_path: str | None = None
    allow_no_auth: bool = False
    web_password: str | None = None
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )
    embedding_provider: Literal["gemini", "local"] = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = Field(default=768, ge=1, le=3072)
    embedding_batch_size: int = Field(default=100, ge=1, le=512)
    embed_inter_batch_delay: float = Field(
        default=0.0,
        ge=0.0,
        le=60.0,
        validation_alias=AliasChoices("DORY_EMBED_INTER_BATCH_DELAY"),
    )
    embed_max_retries: int = Field(
        default=6,
        ge=0,
        le=20,
        validation_alias=AliasChoices("DORY_EMBED_MAX_RETRIES"),
    )
    local_embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_LOCAL_EMBEDDING_API_KEY", "DORY_LOCAL_LLM_API_KEY"),
    )
    local_embedding_base_url: str = "http://127.0.0.1:8000/v1"
    local_embedding_model: str = "qwen3-embed"
    local_embedding_query_instruction: str = "Given a web search query, retrieve relevant passages that answer the query"
    local_embedding_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    sovereign_mode: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3.1-flash-lite-preview"
    openrouter_query_model: str = "google/gemini-2.5-flash-lite"
    openrouter_judge_model: str = "google/gemini-3.1-flash-lite-preview"
    openrouter_dream_model: str = "google/gemini-3.1-flash-lite-preview"
    openrouter_maintenance_model: str = "google/gemini-3.1-flash-lite-preview"
    openrouter_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    openrouter_reasoning_effort: str = "low"
    local_llm_api_key: str | None = None
    local_llm_base_url: str = "http://127.0.0.1:11434/v1"
    local_llm_model: str = "qwen3.5:4b"
    local_llm_timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)
    local_llm_max_tokens: int = Field(default=512, ge=64, le=2048)
    active_memory_llm_provider: Literal["openrouter", "local", "auto", "off"] = "openrouter"
    active_memory_llm_stages: Literal["both", "plan", "compose"] = "both"
    migration_concurrency: int = Field(default=max(2, min(8, cpu_count() or 4)), ge=1, le=64)
    query_planner_enabled: bool = False
    query_expansion_enabled: bool = False
    query_expansion_max: int = Field(default=2, ge=0, le=5)
    query_reranker_enabled: bool = False
    query_reranker_provider: Literal["openrouter", "local"] = "openrouter"
    query_reranker_candidate_limit: int = Field(default=40, ge=2, le=100)
    local_reranker_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_LOCAL_RERANKER_API_KEY", "DORY_LOCAL_LLM_API_KEY"),
    )
    local_reranker_base_url: str = "http://127.0.0.1:8000/v1"
    local_reranker_model: str = "qwen3-rerank"
    local_reranker_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    eval_judge_enabled: bool = True
    max_write_bytes: int = Field(default=10_240, ge=1)
    default_wake_budget_tokens: int = Field(default=600, ge=1, le=1500)
    migrate_progress: bool = False
    openrouter_input_price_per_million: float | None = None
    openrouter_output_price_per_million: float | None = None
    claude_projects_root: str | None = None
    codex_sessions_root: str | None = None
    opencode_db_path: str | None = None
    openclaw_agents_root: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_OPENCLAW_AGENTS_ROOT", "DORY_OPENCLAW_SESSIONS_ROOT"),
    )
    hermes_sessions_root: str | None = None
    hermes_state_db_path: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path


def resolve_runtime_paths(
    *,
    corpus_root: Path | None = None,
    index_root: Path | None = None,
    auth_tokens_path: Path | None = None,
) -> RuntimePaths:
    settings = DorySettings()
    resolved_corpus_root = Path(corpus_root or settings.corpus_root or settings.root)
    resolved_index_root = Path(index_root or settings.index_root or (resolved_corpus_root / ".index"))
    resolved_auth_tokens_path = Path(
        auth_tokens_path or settings.auth_tokens_path or (resolved_corpus_root / ".dory" / "auth-tokens.json")
    )
    return RuntimePaths(
        corpus_root=resolved_corpus_root,
        index_root=resolved_index_root,
        auth_tokens_path=resolved_auth_tokens_path,
    )
