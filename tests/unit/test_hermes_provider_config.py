from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_provider_module():
    provider_path = Path("plugins/hermes-dory/provider.py")
    spec = importlib.util.spec_from_file_location("hermes_dory_provider", provider_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load provider module from {provider_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hermes_provider_config_loads_from_env() -> None:
    module = _load_provider_module()
    config = module.HermesDoryProviderConfig.from_env(
        {
            "DORY_HTTP_URL": "http://dory.local:8766",
            "DORY_HTTP_TOKEN": "secret",
            "DORY_HERMES_AGENT": "assistant-hermes",
            "DORY_HERMES_MEMORY_MODE": "tools",
            "DORY_HERMES_WAKE_BUDGET_TOKENS": "720",
            "DORY_HERMES_WAKE_PROFILE": "writing",
            "DORY_HERMES_WAKE_RECENT_SESSIONS": "7",
            "DORY_HERMES_WAKE_INCLUDE_PINNED_DECISIONS": "false",
            "DORY_HERMES_ACTIVE_MEMORY_INCLUDE_WAKE": "true",
            "DORY_HERMES_SEARCH_K": "11",
            "DORY_HERMES_SEARCH_MODE": "exact",
        }
    )

    assert config.base_url == "http://dory.local:8766"
    assert config.token == "secret"
    assert config.default_agent == "assistant-hermes"
    assert config.memory_mode == "tools"
    assert config.wake_budget_tokens == 720
    assert config.wake_profile == "writing"
    assert config.wake_recent_sessions == 7
    assert config.wake_include_pinned_decisions is False
    assert config.active_memory_include_wake is True
    assert config.search_k == 11
    assert config.search_mode == "exact"


def test_hermes_provider_config_loads_from_hermes_yaml_and_env_defaults(tmp_path: Path) -> None:
    module = _load_provider_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
memory:
  provider: dory
  providers:
    dory:
      base_url: http://mini.lan:8766
      token: yaml-token
      default_agent: hermes-main
      memory_mode: context
      wake_budget_tokens: 480
      wake_profile: privacy
      wake_recent_sessions: 3
      wake_include_pinned_decisions: false
      active_memory_include_wake: true
      search_k: 6
      search_mode: semantic
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = module.HermesDoryProviderConfig.from_hermes_config(
        config_path,
        env={"DORY_HTTP_URL": "http://fallback:8766", "DORY_HERMES_SEARCH_MODE": "hybrid"},
    )

    assert config.base_url == "http://mini.lan:8766"
    assert config.token == "yaml-token"
    assert config.default_agent == "hermes-main"
    assert config.memory_mode == "context"
    assert config.wake_budget_tokens == 480
    assert config.wake_profile == "privacy"
    assert config.wake_recent_sessions == 3
    assert config.wake_include_pinned_decisions is False
    assert config.active_memory_include_wake is True
    assert config.search_k == 6
    assert config.search_mode == "semantic"


def test_hermes_provider_prefers_native_dory_yaml(tmp_path: Path) -> None:
    module = _load_provider_module()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "dory.yaml").write_text(
        """
base_url: http://dory.native:8766
default_agent: hermes-native
memory_mode: hybrid
search_mode: bm25
wake_profile: coding
active_memory_include_wake: false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (hermes_home / "config.yaml").write_text(
        """
memory:
  provider: dory
  providers:
    dory:
      base_url: http://dory.fallback:8766
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = module.HermesDoryProviderConfig.from_hermes_config(hermes_home=hermes_home)

    assert config.base_url == "http://dory.native:8766"
    assert config.default_agent == "hermes-native"
    assert config.memory_mode == "hybrid"
    assert config.search_mode == "bm25"
    assert config.wake_profile == "coding"
    assert config.active_memory_include_wake is False


def test_hermes_provider_normalizes_legacy_search_modes_before_http_request() -> None:
    module = _load_provider_module()
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True}

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return _FakeResponse()

    provider = module.DoryMemoryProvider(
        base_url="http://dory.local:8766",
        client=_FakeClient(),
        search_mode="semantic",
    )

    provider.search("who is Casey")
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/search"
    assert captured["json"] == {"query": "who is Casey", "k": 8, "mode": "vector"}

    provider.search("latest active work", mode="lexical")
    assert captured["json"] == {"query": "latest active work", "k": 8, "mode": "bm25"}

    provider.search("latest active work", mode="text")
    assert captured["json"] == {"query": "latest active work", "k": 8, "mode": "bm25"}

    provider.search("unique marker", mode="exact")
    assert captured["json"] == {"query": "unique marker", "k": 8, "mode": "exact"}


def test_hermes_provider_accepts_api_native_search_modes() -> None:
    module = _load_provider_module()
    assert module._safe_search_mode("bm25", default="hybrid") == "bm25"
    assert module._safe_search_mode("text", default="hybrid") == "text"
    assert module._safe_search_mode("vector", default="hybrid") == "vector"
    assert module._safe_search_mode("exact", default="hybrid") == "exact"
    assert module._normalize_search_mode("bm25") == "bm25"
    assert module._normalize_search_mode("text") == "bm25"
    assert module._normalize_search_mode("vector") == "vector"
    assert module._normalize_search_mode("exact") == "exact"


def test_hermes_provider_tool_schema_exposes_finalized_dory_surface() -> None:
    module = _load_provider_module()
    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766")
    schemas = {schema["name"]: schema for schema in provider.get_tool_schemas()}

    assert {"dory_research", "dory_purge"} <= set(schemas)
    assert "exact" in schemas["dory_search"]["parameters"]["properties"]["mode"]["enum"]
    assert "text" in schemas["dory_search"]["parameters"]["properties"]["mode"]["enum"]
    assert schemas["dory_search"]["parameters"]["properties"]["corpus"]["enum"] == ["durable", "sessions", "all"]
    assert "scope" in schemas["dory_search"]["parameters"]["properties"]
    assert "include_content" in schemas["dory_search"]["parameters"]["properties"]
    assert "profile" in schemas["dory_wake"]["parameters"]["properties"]
    assert "include_wake" in schemas["dory_active_memory"]["parameters"]["properties"]
    assert "dry_run" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "force_inbox" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "allow_canonical" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "expected_hash" in schemas["dory_write"]["parameters"]["properties"]
    assert "expected_hash" in schemas["dory_write"]["description"]
    assert schemas["dory_purge"]["parameters"]["properties"]["dry_run"]["default"] is True


def test_hermes_plugin_manifest_exists() -> None:
    manifest_path = Path("plugins/hermes-dory/plugin.yaml")
    assert manifest_path.exists()
    content = manifest_path.read_text(encoding="utf-8")

    assert "name: dory" in content
    assert "on_session_end" in content
    assert "on_memory_write" in content
