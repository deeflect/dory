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
        extra="ignore",
    )

    root: str = "."
    corpus_root: str | None = None
    index_root: str | None = None
    auth_tokens_path: str | None = None
    allow_no_auth: bool = False
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DORY_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = Field(default=768, ge=1, le=3072)
    embedding_batch_size: int = Field(default=100, ge=1, le=512)
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
    eval_judge_enabled: bool = True
    max_write_bytes: int = Field(default=10_240, ge=1)
    default_wake_budget_tokens: int = Field(default=600, ge=1, le=1500)


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
