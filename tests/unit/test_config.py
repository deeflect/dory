from __future__ import annotations

from pathlib import Path

from dory_core.config import DorySettings, resolve_runtime_paths


def test_settings_defaults() -> None:
    settings = DorySettings()

    assert settings.default_wake_budget_tokens == 600
    assert settings.embedding_provider == "gemini"
    assert settings.embedding_model == "gemini-embedding-001"
    assert settings.embedding_dimensions == 768
    assert settings.local_embedding_base_url == "http://127.0.0.1:8000/v1"
    assert settings.local_embedding_model == "qwen3-embed"
    assert settings.local_embedding_query_instruction == (
        "Given a web search query, retrieve relevant passages that answer the query"
    )
    assert settings.max_write_bytes == 10_240
    assert settings.http_host == "127.0.0.1"
    assert settings.http_port == 8000
    assert settings.allow_no_auth is False
    assert settings.query_planner_enabled is False
    assert settings.query_expansion_enabled is False
    assert settings.query_reranker_enabled is False
    assert settings.query_reranker_provider == "openrouter"
    assert settings.query_reranker_candidate_limit == 40
    assert settings.local_reranker_base_url == "http://127.0.0.1:8000/v1"
    assert settings.local_reranker_model == "qwen3-rerank"
    assert settings.active_memory_llm_provider == "openrouter"
    assert settings.local_llm_base_url == "http://127.0.0.1:11434/v1"
    assert settings.local_llm_model == "qwen3.5:4b"
    assert settings.local_llm_max_tokens == 512
    assert settings.dream_llm_provider == "openrouter"
    assert settings.active_memory_llm_stages == "both"


def test_resolve_runtime_paths_defaults_to_root_relative_layout(monkeypatch) -> None:
    monkeypatch.delenv("DORY_ROOT", raising=False)
    monkeypatch.delenv("DORY_CORPUS_ROOT", raising=False)
    monkeypatch.delenv("DORY_INDEX_ROOT", raising=False)
    monkeypatch.delenv("DORY_AUTH_TOKENS_PATH", raising=False)

    paths = resolve_runtime_paths()

    assert paths.corpus_root == Path(".")
    assert paths.index_root == Path(".index")
    assert paths.auth_tokens_path == Path(".dory/auth-tokens.json")


def test_resolve_runtime_paths_prefers_explicit_corpus_env(monkeypatch) -> None:
    monkeypatch.setenv("DORY_ROOT", "/var/lib/dory")
    monkeypatch.setenv("DORY_CORPUS_ROOT", "/srv/dory")
    monkeypatch.delenv("DORY_INDEX_ROOT", raising=False)
    monkeypatch.delenv("DORY_AUTH_TOKENS_PATH", raising=False)

    paths = resolve_runtime_paths()

    assert paths.corpus_root == Path("/srv/dory")
    assert paths.index_root == Path("/srv/dory/.index")
    assert paths.auth_tokens_path == Path("/srv/dory/.dory/auth-tokens.json")


def test_settings_accept_google_api_key_alias(monkeypatch) -> None:
    monkeypatch.delenv("DORY_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    settings = DorySettings()

    assert settings.gemini_api_key == "test-key"


def test_settings_accept_query_planner_toggle(monkeypatch) -> None:
    monkeypatch.setenv("DORY_QUERY_PLANNER_ENABLED", "true")

    settings = DorySettings()

    assert settings.query_planner_enabled is True


def test_settings_accept_query_expansion_toggle(monkeypatch) -> None:
    monkeypatch.setenv("DORY_QUERY_EXPANSION_ENABLED", "true")

    settings = DorySettings()

    assert settings.query_expansion_enabled is True


def test_settings_accept_query_reranker_toggle(monkeypatch) -> None:
    monkeypatch.setenv("DORY_QUERY_RERANKER_ENABLED", "true")

    settings = DorySettings()

    assert settings.query_reranker_enabled is True


def test_settings_accept_local_embedding_and_reranker(monkeypatch) -> None:
    monkeypatch.setenv("DORY_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("DORY_LOCAL_EMBEDDING_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("DORY_LOCAL_EMBEDDING_MODEL", "qwen3-embed")
    monkeypatch.setenv("DORY_LOCAL_EMBEDDING_QUERY_INSTRUCTION", "Find matching memory notes")
    monkeypatch.setenv("DORY_LOCAL_EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setenv("DORY_QUERY_RERANKER_PROVIDER", "local")
    monkeypatch.setenv("DORY_LOCAL_RERANKER_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("DORY_LOCAL_RERANKER_MODEL", "qwen3-rerank")
    monkeypatch.setenv("DORY_LOCAL_RERANKER_API_KEY", "rerank-key")

    settings = DorySettings()

    assert settings.embedding_provider == "local"
    assert settings.local_embedding_base_url == "https://llm.example.test/v1"
    assert settings.local_embedding_model == "qwen3-embed"
    assert settings.local_embedding_query_instruction == "Find matching memory notes"
    assert settings.local_embedding_api_key == "embed-key"
    assert settings.query_reranker_provider == "local"
    assert settings.local_reranker_base_url == "https://llm.example.test/v1"
    assert settings.local_reranker_model == "qwen3-rerank"
    assert settings.local_reranker_api_key == "rerank-key"


def test_settings_accept_local_active_memory_llm(monkeypatch) -> None:
    monkeypatch.setenv("DORY_ACTIVE_MEMORY_LLM_PROVIDER", "local")
    monkeypatch.setenv("DORY_DREAM_LLM_PROVIDER", "local")
    monkeypatch.setenv("DORY_LOCAL_LLM_BASE_URL", "https://llm.example.test")
    monkeypatch.setenv("DORY_LOCAL_LLM_MODEL", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("DORY_LOCAL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DORY_LOCAL_LLM_MAX_TOKENS", "256")
    monkeypatch.setenv("DORY_ACTIVE_MEMORY_LLM_STAGES", "compose")

    settings = DorySettings()

    assert settings.active_memory_llm_provider == "local"
    assert settings.dream_llm_provider == "local"
    assert settings.local_llm_base_url == "https://llm.example.test"
    assert settings.local_llm_model == "Qwen3.5-4B-4bit"
    assert settings.local_llm_api_key == "test-key"
    assert settings.local_llm_max_tokens == 256
    assert settings.active_memory_llm_stages == "compose"


def test_settings_accept_allow_no_auth_toggle(monkeypatch) -> None:
    monkeypatch.setenv("DORY_ALLOW_NO_AUTH", "true")

    settings = DorySettings()

    assert settings.allow_no_auth is True


def test_settings_ignore_empty_optional_env_values(monkeypatch) -> None:
    monkeypatch.setenv("DORY_LOCAL_EMBEDDING_TIMEOUT_SECONDS", "")
    monkeypatch.setenv("DORY_ACTIVE_MEMORY_LLM_PROVIDER", "")
    monkeypatch.setenv("DORY_LOCAL_LLM_TIMEOUT_SECONDS", "")
    monkeypatch.setenv("DORY_LOCAL_RERANKER_TIMEOUT_SECONDS", "")

    settings = DorySettings()

    assert settings.local_embedding_timeout_seconds == 30.0
    assert settings.active_memory_llm_provider == "openrouter"
    assert settings.local_llm_timeout_seconds == 5.0
    assert settings.local_reranker_timeout_seconds == 30.0
