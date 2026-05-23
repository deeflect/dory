from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from dory_http.app import build_app
from dory_mcp.tools import build_tool_schemas


def test_http_and_mcp_search_contracts_keep_agent_fields_in_sync(tmp_path: Path) -> None:
    http_components = _http_components(tmp_path)
    http_schema = http_components["SearchReq"]
    mcp_schema = _mcp_schema("dory_search")

    assert http_schema["required"] == mcp_schema["required"] == ["query"]
    _assert_matching_fields(
        http_schema,
        mcp_schema,
        fields=("k", "mode", "corpus", "min_relevance_score", "include_content", "rerank", "debug"),
    )
    assert _enum(http_schema["properties"]["corpus"]) == ["durable", "sessions", "all"]
    assert _enum(mcp_schema["properties"]["corpus"]) == ["durable", "sessions", "all"]
    assert _scope_fields(http_schema, http_components=http_components) == _scope_fields(mcp_schema)
    assert "session_key" in _scope_fields(mcp_schema)


def test_http_and_mcp_active_memory_contracts_keep_agent_fields_in_sync(tmp_path: Path) -> None:
    http_components = _http_components(tmp_path)
    http_schema = http_components["ActiveMemoryReq"]
    mcp_schema = _mcp_schema("dory_active_memory")

    assert http_schema["required"] == mcp_schema["required"] == ["prompt", "agent"]
    _assert_matching_fields(
        http_schema,
        mcp_schema,
        fields=("profile", "timeout_ms", "budget_tokens", "include_wake", "rerank", "debug", "partial_ok"),
    )
    assert "project" in http_schema["properties"]
    assert "project" in mcp_schema["properties"]
    assert _scope_fields(http_schema, http_components=http_components) == _scope_fields(mcp_schema)
    assert _enum(http_schema["properties"]["rerank"]) == ["auto", "true", "false"]


def test_http_and_mcp_memory_write_contracts_keep_recovery_fields_in_sync(tmp_path: Path) -> None:
    http_schema = _http_components(tmp_path)["MemoryWriteReq"]
    mcp_schema = _mcp_schema("dory_memory_write")

    assert http_schema["required"] == mcp_schema["required"] == ["action", "kind", "subject", "content"]
    _assert_matching_fields(
        http_schema,
        mcp_schema,
        fields=(
            "action",
            "kind",
            "confidence",
            "source",
            "soft",
            "dry_run",
            "force_inbox",
            "allow_canonical",
            "agent",
            "session_id",
            "origin_surface",
        ),
    )
    assert _enum(http_schema["properties"]["action"]) == ["write", "replace", "forget"]
    assert set(_enum(mcp_schema["properties"]["confidence"]) or []) == {"high", "medium", "low"}


def _http_components(tmp_path: Path) -> dict[str, Any]:
    client = TestClient(build_app(tmp_path / "corpus", tmp_path / "index"))
    return client.get("/openapi.json").json()["components"]["schemas"]


def _mcp_schema(tool_name: str) -> dict[str, Any]:
    tools = {tool["name"]: tool["inputSchema"] for tool in build_tool_schemas()}
    return tools[tool_name]


def _assert_matching_fields(http_schema: dict[str, Any], mcp_schema: dict[str, Any], *, fields: tuple[str, ...]) -> None:
    for field in fields:
        http_field = http_schema["properties"][field]
        mcp_field = mcp_schema["properties"][field]
        assert _enum(http_field) == _enum(mcp_field), field
        for key in ("minimum", "maximum"):
            if key in http_field or key in mcp_field:
                assert http_field.get(key) == mcp_field.get(key), field
        if "default" in http_field or "default" in mcp_field:
            assert http_field.get("default") == mcp_field.get("default"), field
        assert _field_type(http_field) == _field_type(mcp_field), field


def _scope_fields(schema: dict[str, Any], *, http_components: dict[str, Any] | None = None) -> set[str]:
    scope = schema["properties"]["scope"]
    if "$ref" in scope:
        # The only HTTP ref used here is SearchScope. Resolve just enough to
        # keep this parity test compact and focused on field names.
        assert http_components is not None
        scope = http_components["SearchScope"]
    return set(scope["properties"])


def _enum(field: dict[str, Any]) -> list[str] | None:
    if "enum" in field:
        return field["enum"]
    for option in field.get("anyOf", []):
        if "enum" in option:
            return option["enum"]
    return None


def _field_type(field: dict[str, Any]) -> str | None:
    if "type" in field:
        return field["type"]
    for option in field.get("anyOf", []):
        if option.get("type") != "null":
            return option.get("type")
    if "$ref" in field:
        return "object"
    return None
