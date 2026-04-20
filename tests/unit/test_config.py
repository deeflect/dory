from __future__ import annotations

from pathlib import Path

from dory_core.config import DorySettings, resolve_runtime_paths


def test_settings_defaults() -> None:
    settings = DorySettings()

    assert settings.default_wake_budget_tokens == 600
    assert settings.embedding_model == "gemini-embedding-001"
    assert settings.embedding_dimensions == 768
    assert settings.max_write_bytes == 10_240
    assert settings.http_host == "127.0.0.1"
    assert settings.http_port == 8000
    assert settings.allow_no_auth is False
    assert settings.query_planner_enabled is False
    assert settings.query_expansion_enabled is False
    assert settings.query_reranker_enabled is False


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


def test_settings_accept_allow_no_auth_toggle(monkeypatch) -> None:
    monkeypatch.setenv("DORY_ALLOW_NO_AUTH", "true")

    settings = DorySettings()

    assert settings.allow_no_auth is True
