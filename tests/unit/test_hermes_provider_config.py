from __future__ import annotations

import importlib.util
import json
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

    provider.search("benchmark query", rerank="false", debug=True)
    assert captured["json"] == {
        "query": "benchmark query",
        "k": 8,
        "mode": "vector",
        "rerank": "false",
        "debug": True,
    }

    provider.active_memory(
        "benchmark active memory",
        project="dory",
        scope={"session_key": "hermes-session"},
        include_wake=False,
        rerank="true",
    )
    assert captured["path"] == "/v1/active-memory"
    assert captured["json"] == {
        "prompt": "benchmark active memory",
        "agent": "hermes",
        "budget_tokens": 600,
        "include_wake": False,
        "project": "dory",
        "scope": {"session_key": "hermes-session"},
        "rerank": "true",
    }


def test_hermes_prefetch_bundle_forwards_project_to_wake_and_active_memory() -> None:
    module = _load_provider_module()
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True}

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            requests.append({"method": method, "path": path, "json": kwargs.get("json")})
            return _FakeResponse()

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    provider.prefetch_bundle("what changed", project="dory", scope={"session_key": "hermes-session"})

    wake_request = requests[0]
    search_request = requests[1]
    active_memory_request = requests[2]
    assert wake_request["path"] == "/v1/wake"
    assert wake_request["json"]["project"] == "dory"  # type: ignore[index]
    assert search_request["path"] == "/v1/search"
    assert search_request["json"]["scope"] == {"session_key": "hermes-session"}  # type: ignore[index]
    assert active_memory_request["path"] == "/v1/active-memory"
    assert active_memory_request["json"]["project"] == "dory"  # type: ignore[index]
    assert active_memory_request["json"]["scope"] == {"session_key": "hermes-session"}  # type: ignore[index]


def test_hermes_casual_prefetch_uses_casual_profile_and_skips_retrieved_evidence() -> None:
    module = _load_provider_module()
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            requests.append({"method": method, "path": path, "json": kwargs.get("json")})
            if path == "/v1/wake":
                return _FakeResponse({"block": "## Wake\nCasual context only."})
            if path == "/v1/active-memory":
                return _FakeResponse({"kind": "none", "block": ""})
            if path == "/v1/search":
                return _FakeResponse(
                    {
                        "results": [
                            {"path": "projects/dory/state.md", "snippet": "stale project evidence"},
                        ]
                    }
                )
            return _FakeResponse({"ok": True})

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())
    provider.initialize("session-123", platform="telegram")

    memory_section = provider.build_memory_section("what's up bro")

    paths = [request["path"] for request in requests]
    assert paths == ["/v1/wake", "/v1/active-memory"]
    wake_request = requests[0]
    active_memory_request = requests[1]
    assert wake_request["json"]["profile"] == "casual"  # type: ignore[index]
    assert active_memory_request["json"]["profile"] == "casual"  # type: ignore[index]
    assert "Retrieved Evidence" not in memory_section
    assert "projects/dory/state.md" not in memory_section


def test_hermes_prefetch_bundle_exposes_injection_trace() -> None:
    module = _load_provider_module()

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            if path == "/v1/wake":
                return _FakeResponse({"block": "## Wake", "sources": ["profiles/coding/active.md"]})
            if path == "/v1/search":
                return _FakeResponse(
                    {
                        "results": [
                            {"path": "projects/dory/state.md", "snippet": "Dory status"},
                            {"path": "projects/dory/state.md", "snippet": "duplicate"},
                            {"path": "knowledge/dev/hermes.md", "snippet": "Hermes"},
                        ]
                    }
                )
            if path == "/v1/active-memory":
                return _FakeResponse(
                    {
                        "kind": "memory",
                        "block": "## Active memory",
                        "profile": "coding",
                        "sources": ["core/active.md", "knowledge/dev/hermes.md"],
                    }
                )
            return _FakeResponse({"ok": True})

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    prefetched = provider.prefetch_bundle("fix the memory leak", scope={"session_key": "hermes-session"})

    assert prefetched["trace"] == {
        "profile": "coding",
        "include_search": True,
        "search_skipped": False,
        "wake_sources": ["profiles/coding/active.md"],
        "active_memory_sources": ["core/active.md", "knowledge/dev/hermes.md"],
        "search_result_paths": ["projects/dory/state.md", "knowledge/dev/hermes.md"],
        "injected_paths": ["core/active.md", "knowledge/dev/hermes.md", "projects/dory/state.md"],
    }


def test_hermes_casual_prefetch_trace_records_skipped_search() -> None:
    module = _load_provider_module()

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            if path == "/v1/wake":
                return _FakeResponse({"block": "## Wake", "sources": ["profiles/casual/active.md"]})
            if path == "/v1/active-memory":
                return _FakeResponse({"kind": "none", "block": "", "profile": "casual", "sources": []})
            raise AssertionError(f"unexpected request: {method} {path}")

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    prefetched = provider.prefetch_bundle("yo")

    assert prefetched["trace"] == {
        "profile": "casual",
        "include_search": False,
        "search_skipped": True,
        "wake_sources": ["profiles/casual/active.md"],
        "active_memory_sources": [],
        "search_result_paths": [],
        "injected_paths": ["profiles/casual/active.md"],
    }


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



def test_hermes_provider_uses_long_enough_http_timeout_for_active_memory(monkeypatch) -> None:
    module = _load_provider_module()
    captured: dict[str, object] = {}

    class FakeHttpxClient:
        def __init__(self, *, base_url: str, timeout: object) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(module.httpx, "Client", FakeHttpxClient)

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766")

    assert provider._owned_client is not None
    assert captured["base_url"] == "http://dory.local:8766"
    assert captured["timeout"] >= 20.0


def test_hermes_provider_tool_schema_exposes_finalized_dory_surface() -> None:
    module = _load_provider_module()
    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766")
    schemas = {schema["name"]: schema for schema in provider.get_tool_schemas()}

    assert {"dory_research", "dory_publish_research", "dory_purge"} <= set(schemas)
    assert "exact" in schemas["dory_search"]["parameters"]["properties"]["mode"]["enum"]
    assert "text" in schemas["dory_search"]["parameters"]["properties"]["mode"]["enum"]
    assert schemas["dory_search"]["parameters"]["properties"]["corpus"]["enum"] == ["durable", "sessions", "all"]
    assert "scope" in schemas["dory_search"]["parameters"]["properties"]
    assert "include_content" in schemas["dory_search"]["parameters"]["properties"]
    assert schemas["dory_search"]["parameters"]["properties"]["rerank"]["enum"] == ["auto", "true", "false"]
    assert "debug" in schemas["dory_search"]["parameters"]["properties"]
    assert "profile" in schemas["dory_wake"]["parameters"]["properties"]
    assert "enum" not in schemas["dory_wake"]["parameters"]["properties"]["profile"]
    assert "project" in schemas["dory_wake"]["parameters"]["properties"]
    assert "agent" not in schemas["dory_wake"]["parameters"].get("required", [])
    assert "session_key" in schemas["dory_search"]["parameters"]["properties"]["scope"]["properties"]
    assert "include_wake" in schemas["dory_active_memory"]["parameters"]["properties"]
    assert "project" in schemas["dory_active_memory"]["parameters"]["properties"]
    assert "agent" not in schemas["dory_active_memory"]["parameters"].get("required", [])
    assert schemas["dory_active_memory"]["parameters"]["properties"]["rerank"]["enum"] == [
        "auto",
        "true",
        "false",
    ]
    assert "enum" not in schemas["dory_active_memory"]["parameters"]["properties"]["profile"]
    assert "dry_run" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "force_inbox" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "allow_canonical" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "origin_surface" in schemas["dory_memory_write"]["parameters"]["properties"]
    assert "proposal_id" in schemas["dory_memory_propose"]["parameters"]["properties"]
    assert "source_paths" in schemas["dory_memory_propose"]["parameters"]["properties"]
    assert schemas["dory_memory_proposals"]["parameters"]["properties"]["status"]["enum"] == [
        "pending",
        "applied",
        "rejected",
    ]
    assert "reason" in schemas["dory_memory_proposal_reject"]["parameters"]["properties"]
    assert "from_line" in schemas["dory_get"]["parameters"]["properties"]
    assert "expected_hash" in schemas["dory_write"]["parameters"]["properties"]
    assert "expected_hash" in schemas["dory_write"]["description"]
    assert schemas["dory_link"]["parameters"]["properties"]["max_edges"]["default"] == 40
    assert "exclude_prefixes" in schemas["dory_link"]["parameters"]["properties"]
    assert schemas["dory_purge"]["parameters"]["properties"]["dry_run"]["default"] is True
    assert schemas["dory_publish_research"]["parameters"]["properties"]["dry_run"]["default"] is True
    assert schemas["dory_publish_research"]["parameters"]["properties"]["visibility"]["enum"] == [
        "internal",
        "public",
        "private",
    ]


def test_hermes_fallback_tool_schema_accepts_custom_profile_names(monkeypatch) -> None:
    module = _load_provider_module()
    monkeypatch.setattr(module, "_build_canonical_hermes_tool_schemas", lambda: None)
    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766")
    schemas = {schema["name"]: schema for schema in provider.get_tool_schemas()}

    assert schemas["dory_wake"]["parameters"]["properties"]["profile"] == {"type": "string"}
    assert schemas["dory_active_memory"]["parameters"]["properties"]["profile"] == {"type": "string"}


def test_hermes_publish_research_writes_knowledge_markdown_dry_run_by_default() -> None:
    module = _load_provider_module()
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "path": "knowledge/research/2026-04-20-agent-memory.md",
                "action": "would_create",
                "bytes_written": 42,
                "hash": "sha256:abc",
                "indexed": False,
                "edges_added": 0,
            }

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kwargs.get("json")
            return _FakeResponse()

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    payload = json.loads(
        provider.handle_tool_call(
            "dory_publish_research",
            {
                "title": "Agent Memory",
                "question": "What works?",
                "body": "Markdown research body.",
                "sources": ["https://example.test/paper"],
                "tags": ["memory", "agents"],
                "target": "knowledge/research/2026-04-20-agent-memory.md",
            },
        )
    )

    assert payload["action"] == "would_create"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/write"
    request = captured["json"]
    assert isinstance(request, dict)
    assert request["kind"] == "create"
    assert request["target"] == "knowledge/research/2026-04-20-agent-memory.md"
    assert request["dry_run"] is True
    assert request["frontmatter"] == {
        "title": "Agent Memory",
        "type": "knowledge",
        "status": "done",
        "source_kind": "research",
        "temperature": "warm",
        "visibility": "internal",
        "sensitivity": "none",
        "tags": ["memory", "agents"],
    }
    assert "## Question\nWhat works?" in request["content"]
    assert "## Research\nMarkdown research body." in request["content"]
    assert "- https://example.test/paper" in request["content"]


def test_hermes_provider_routes_memory_proposal_tools() -> None:
    module = _load_provider_module()
    requests: list[dict[str, object]] = []

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True}

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            requests.append({"method": method, "path": path, "json": kwargs.get("json")})
            return _FakeResponse()

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    provider.handle_tool_call(
        "dory_memory_propose",
        {
            "action": "write",
            "kind": "fact",
            "subject": "example",
            "content": "Example fact.",
            "proposal_id": "example-proposal",
        },
    )
    provider.handle_tool_call("dory_memory_proposals", {"status": "pending"})
    provider.handle_tool_call("dory_memory_proposal_get", {"proposal_id": "example-proposal"})
    provider.handle_tool_call("dory_memory_proposal_apply", {"proposal_id": "example-proposal"})
    provider.handle_tool_call(
        "dory_memory_proposal_reject",
        {"proposal_id": "example-proposal", "reason": "not durable"},
    )

    assert [request["path"] for request in requests] == [
        "/v1/memory-proposals",
        "/v1/memory-proposals/list",
        "/v1/memory-proposals/get",
        "/v1/memory-proposals/apply",
        "/v1/memory-proposals/reject",
    ]
    assert requests[0]["json"] == {
        "action": "write",
        "kind": "fact",
        "subject": "example",
        "content": "Example fact.",
        "soft": False,
        "force_inbox": False,
        "proposal_id": "example-proposal",
    }
    assert requests[-1]["json"] == {"proposal_id": "example-proposal", "reason": "not durable"}


def test_hermes_provider_tool_errors_are_structured() -> None:
    module = _load_provider_module()

    class _ErrorResponse:
        status_code = 404
        text = '{"detail":"missing memory file"}'

        @staticmethod
        def json() -> dict[str, object]:
            return {"detail": "missing memory file"}

    class _FakeClient:
        def request(self, method: str, path: str, **kwargs):
            del method, path, kwargs
            return _ErrorResponse()

    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766", client=_FakeClient())

    payload = json.loads(provider.handle_tool_call("dory_get", {"path": "missing.md"}))

    assert payload == {
        "ok": False,
        "error": "missing memory file",
        "error_type": "not_found",
        "status_code": 404,
    }


def test_hermes_memory_mirror_uses_date_partitioned_path() -> None:
    module = _load_provider_module()
    provider = module.DoryMemoryProvider(base_url="http://dory.local:8766")

    target = provider._memory_mirror_target()

    assert target.startswith("inbox/hermes-memory-mirror/")
    assert target.endswith(".md")
    assert target != "inbox/hermes-memory-mirror.md"


def test_hermes_plugin_manifest_exists() -> None:
    manifest_path = Path("plugins/hermes-dory/plugin.yaml")
    assert manifest_path.exists()
    content = manifest_path.read_text(encoding="utf-8")

    assert "name: dory" in content
    assert "on_session_end" in content
    assert "on_memory_write" in content
