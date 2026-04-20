from __future__ import annotations

import importlib.util
from pathlib import Path
import urllib.error


def _load_bridge_module():
    bridge_path = Path("scripts/claude-code/dory-mcp-http-bridge.py").resolve()
    spec = importlib.util.spec_from_file_location("dory_mcp_http_bridge", bridge_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_routes_semantic_memory_write(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"resolved": True, "result": "written", "target_path": "people/anna.md"}

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_memory_write",
        {
            "action": "write",
            "kind": "fact",
            "subject": "anna",
            "content": "Anna prefers async work.",
            "scope": "person",
            "soft": True,
            "dry_run": True,
            "force_inbox": True,
            "allow_canonical": True,
            "agent": "claude-code",
        },
    )

    assert captured["endpoint"] == "/v1/memory-write"
    assert captured["body"] == {
        "action": "write",
        "kind": "fact",
        "subject": "anna",
        "content": "Anna prefers async work.",
        "agent": "claude-code",
        "scope": "person",
        "soft": True,
        "dry_run": True,
        "force_inbox": True,
        "allow_canonical": True,
    }
    assert "target_path" in result


def test_bridge_routes_active_memory(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {
            "kind": "memory",
            "summary": "Durable: Anna prefers async work.",
            "block": "## Active memory",
            "sources": ["core/active.md"],
        }

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_active_memory",
        {
            "prompt": "what are we working on today",
            "agent": "claude-code",
            "budget_tokens": 300,
            "profile": "coding",
            "include_wake": False,
        },
    )

    assert captured["endpoint"] == "/v1/active-memory"
    assert captured["body"] == {
        "prompt": "what are we working on today",
        "agent": "claude-code",
        "budget_tokens": 300,
        "profile": "coding",
        "include_wake": False,
    }
    assert "summary" in result


def test_bridge_routes_search_with_corpus(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"results": [], "count": 0}

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_search",
        {
            "query": "recent session evidence",
            "k": 3,
            "mode": "text",
            "corpus": "sessions",
            "scope": {"type": ["log"], "status": ["active"]},
            "include_content": False,
            "min_score": 0.2,
        },
    )

    assert captured["endpoint"] == "/v1/search"
    assert captured["body"] == {
        "query": "recent session evidence",
        "k": 3,
        "mode": "text",
        "corpus": "sessions",
        "scope": {"type": ["log"], "status": ["active"]},
        "include_content": False,
        "min_score": 0.2,
    }
    assert "results" in result


def test_bridge_routes_research(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"artifact": None, "sources": ["core/active.md"]}

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_research",
        {
            "question": "What are the current Dory priorities?",
            "kind": "briefing",
            "corpus": "durable",
            "limit": 4,
            "save": False,
        },
    )

    assert captured["endpoint"] == "/v1/research"
    assert captured["body"] == {
        "question": "What are the current Dory priorities?",
        "kind": "briefing",
        "corpus": "durable",
        "limit": 4,
        "save": False,
    }
    assert "core/active.md" in result


def test_bridge_routes_get_with_native_from_parameter(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_get(endpoint: str):
        captured["endpoint"] = endpoint
        return {"path": "core/user.md", "from": 7}

    monkeypatch.setattr(bridge, "http_get", fake_http_get)

    result = bridge.handle_tool_call("dory_get", {"path": "core/user.md", "from": 7, "lines": 3})

    assert captured["endpoint"] == "/v1/get?path=core%2Fuser.md&from=7&lines=3"
    assert '"from": 7' in result


def test_bridge_retains_legacy_write_route(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"path": "people/anna.md"}

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_write",
        {
            "kind": "append",
            "target": "people/anna.md",
            "content": "Legacy write still works.",
            "soft": True,
            "dry_run": True,
        },
    )

    assert captured["endpoint"] == "/v1/write"
    assert captured["body"] == {
        "kind": "append",
        "target": "people/anna.md",
        "content": "Legacy write still works.",
        "agent": "claude-code",
        "soft": True,
        "dry_run": True,
    }
    assert "people/anna.md" in result


def test_bridge_routes_purge(monkeypatch) -> None:
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    def fake_http_post(endpoint: str, body=None):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"path": "inbox/probe.md", "action": "would_purge"}

    monkeypatch.setattr(bridge, "http_post", fake_http_post)

    result = bridge.handle_tool_call(
        "dory_purge",
        {
            "target": "inbox/probe.md",
            "dry_run": True,
            "expected_hash": "sha256:abc",
            "reason": "cleanup",
            "include_related_tombstone": True,
        },
    )

    assert captured["endpoint"] == "/v1/purge"
    assert captured["body"] == {
        "target": "inbox/probe.md",
        "expected_hash": "sha256:abc",
        "reason": "cleanup",
        "dry_run": True,
        "include_related_tombstone": True,
    }
    assert "would_purge" in result


def test_bridge_prefers_server_tool_schema(monkeypatch) -> None:
    bridge = _load_bridge_module()
    server_tools = [
        {
            "name": "dory_server_defined",
            "description": "server schema",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    monkeypatch.setattr(bridge, "http_get", lambda endpoint: {"tools": server_tools})

    assert bridge.list_tools() == server_tools


def test_bridge_falls_back_to_local_tool_schema(monkeypatch) -> None:
    bridge = _load_bridge_module()

    monkeypatch.setattr(bridge, "http_get", lambda endpoint: {"ok": False, "error": "missing"})

    assert any(tool["name"] == "dory_purge" for tool in bridge.list_tools())


def test_bridge_forwards_bearer_token_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DORY_HTTP_URL", "http://127.0.0.1:8766")
    monkeypatch.setenv("DORY_CLIENT_AUTH_TOKEN", "secret-token")
    bridge = _load_bridge_module()
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request, timeout=0):
        captured["authorization"] = request.headers.get("Authorization")
        captured["url"] = request.full_url
        return _Response()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)

    bridge.http_get("/v1/status")

    assert captured["url"] == "http://127.0.0.1:8766/v1/status"
    assert captured["authorization"] == "Bearer secret-token"


def test_bridge_defaults_to_localhost_when_url_env_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("DORY_HTTP_URL", raising=False)
    bridge = _load_bridge_module()

    assert bridge.DORY_URL == "http://127.0.0.1:8766"


def test_bridge_http_errors_are_structured(monkeypatch) -> None:
    bridge = _load_bridge_module()

    class _Response:
        def __init__(self, status: int = 503, reason: str = "Service Unavailable") -> None:
            self.status = status
            self.reason = reason

        def read(self) -> bytes:
            return b'{"detail":"backend unavailable"}'

        def close(self) -> None:
            return None

    def fake_urlopen(request, timeout=0):
        del request, timeout
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8766/v1/search",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=_Response(),
        )

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)

    payload = bridge.http_post("/v1/search", {"query": "anna"})

    assert payload["ok"] is False
    assert payload["error"]["type"] == "http_error"
    assert payload["error"]["status"] == 503
