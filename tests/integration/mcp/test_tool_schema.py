from __future__ import annotations

from dory_mcp.tools import TOOL_MAP, build_tool_schemas


def test_tool_schema_uses_native_dory_names() -> None:
    tools = build_tool_schemas()

    assert {tool["name"] for tool in tools} == set(TOOL_MAP)


def test_dory_memory_write_schema_exposes_semantic_fields() -> None:
    tools = build_tool_schemas()
    write_tool = next(tool for tool in tools if tool["name"] == "dory_memory_write")

    assert "action" in write_tool["inputSchema"]["required"]
    assert "subject" in write_tool["inputSchema"]["required"]
    assert "confidence" in write_tool["inputSchema"]["properties"]
    assert "source" in write_tool["inputSchema"]["properties"]
    assert "soft" in write_tool["inputSchema"]["properties"]
    assert "dry_run" in write_tool["inputSchema"]["properties"]
    assert "force_inbox" in write_tool["inputSchema"]["properties"]
    assert "allow_canonical" in write_tool["inputSchema"]["properties"]


def test_dory_wake_schema_exposes_profiles() -> None:
    tools = build_tool_schemas()
    wake_tool = next(tool for tool in tools if tool["name"] == "dory_wake")

    assert wake_tool["inputSchema"]["properties"]["profile"]["enum"] == [
        "default",
        "casual",
        "coding",
        "writing",
        "privacy",
    ]


def test_dory_active_memory_schema_exposes_include_wake_and_limits() -> None:
    tools = build_tool_schemas()
    active_tool = next(tool for tool in tools if tool["name"] == "dory_active_memory")
    props = active_tool["inputSchema"]["properties"]

    assert "include_wake" in props
    assert props["rerank"]["enum"] == ["auto", "true", "false"]
    assert props["profile"]["enum"] == ["auto", "general", "coding", "writing", "privacy", "personal"]
    assert props["budget_tokens"]["maximum"] == 1200
    assert props["timeout_ms"]["maximum"] == 5000


def test_dory_search_schema_exposes_min_score() -> None:
    tools = build_tool_schemas()
    search_tool = next(tool for tool in tools if tool["name"] == "dory_search")

    assert "min_score" in search_tool["inputSchema"]["properties"]
    assert "corpus" in search_tool["inputSchema"]["properties"]
    assert search_tool["inputSchema"]["properties"]["corpus"]["enum"] == ["durable", "sessions", "all"]
    assert "scope" in search_tool["inputSchema"]["properties"]
    assert "include_content" in search_tool["inputSchema"]["properties"]
    assert search_tool["inputSchema"]["properties"]["rerank"]["enum"] == ["auto", "true", "false"]
    assert "debug" in search_tool["inputSchema"]["properties"]
    assert "exact" in search_tool["inputSchema"]["properties"]["mode"]["enum"]
    assert "text" in search_tool["inputSchema"]["properties"]["mode"]["enum"]


def test_dory_write_schema_retains_legacy_path_fields() -> None:
    tools = build_tool_schemas()
    write_tool = next(tool for tool in tools if tool["name"] == "dory_write")
    props = write_tool["inputSchema"]["properties"]

    assert "target" in write_tool["inputSchema"]["required"]
    assert props["kind"]["enum"] == ["append", "create", "replace", "forget"]
    assert "soft" in props
    assert "dry_run" in props
    assert "content" not in write_tool["inputSchema"]["required"]
    assert "frontmatter.title" in write_tool["description"]
    assert "frontmatter.type" in write_tool["description"]
    assert "type='capture'" in write_tool["description"]
    assert "type='note'" in write_tool["description"]
    assert "expected_hash" in write_tool["description"]
    assert "forget also requires reason" in write_tool["description"]


def test_dory_purge_schema_exposes_destructive_guards() -> None:
    tools = build_tool_schemas()
    purge_tool = next(tool for tool in tools if tool["name"] == "dory_purge")
    props = purge_tool["inputSchema"]["properties"]

    assert purge_tool["inputSchema"]["required"] == ["target"]
    assert "expected_hash" in props
    assert "reason" in props
    assert props["dry_run"]["default"] is True
    assert "allow_canonical" in props
    assert "include_related_tombstone" in props


def test_dory_link_schema_constrains_direction_values() -> None:
    tools = build_tool_schemas()
    link_tool = next(tool for tool in tools if tool["name"] == "dory_link")
    props = link_tool["inputSchema"]["properties"]

    assert props["direction"]["enum"] == ["out", "in", "both"]
    assert props["max_edges"]["default"] == 40
    assert props["max_edges"]["maximum"] == 500
    assert "exclude_prefixes" in props
