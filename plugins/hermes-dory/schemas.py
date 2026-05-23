from __future__ import annotations

from copy import deepcopy
from typing import Any


def _build_tool_schemas() -> list[dict[str, Any]]:
    # Check for monkeypatched version from provider module (used by tests).
    # The provider re-exports this function; but `_build_canonical_hermes_tool_schemas`
    # is resolved at call time so monkeypatching the provider module works.
    import sys as _sys_mod

    builder = _build_canonical_hermes_tool_schemas
    if "hermes_dory_provider" in _sys_mod.modules:
        provider_mod = _sys_mod.modules["hermes_dory_provider"]
        if hasattr(provider_mod, "_build_canonical_hermes_tool_schemas"):
            candidate = provider_mod._build_canonical_hermes_tool_schemas
            if candidate is not None:
                builder = candidate
    canonical_tools = builder()
    if canonical_tools is not None:
        return [*canonical_tools, _publish_research_tool_schema()]

    return [
        {
            "name": "dory_wake",
            "description": "Build the frozen wake-up block. Use profile='coding' for agent work, 'writing' for voice/content, or 'privacy' for boundary questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_tokens": {"type": "integer"},
                    "agent": {"type": "string"},
                    "project": {"type": "string"},
                    "cwd": {"type": "string"},
                    "profile": {"type": "string"},
                    "include_recent_sessions": {"type": "integer"},
                    "include_pinned_decisions": {"type": "boolean"},
                },
            },
        },
        {
            "name": "dory_active_memory",
            "description": "Run the bounded active-memory pre-reply pass. Limits: budget_tokens <= 1200, timeout_ms <= 30000. Set include_wake=false if wake was already called.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent": {"type": "string"},
                    "cwd": {"type": "string"},
                    "project": {"type": "string"},
                    "scope": {
                        "type": "object",
                        "properties": {
                            "agent": {"type": "array", "items": {"type": "string"}},
                            "device": {"type": "array", "items": {"type": "string"}},
                            "session_id": {"type": "array", "items": {"type": "string"}},
                            "session_key": {"type": "string"},
                            "status": {"type": "array", "items": {"type": "string"}},
                            "since": {"type": "string"},
                            "until": {"type": "string"},
                        },
                    },
                    "profile": {"type": "string"},
                    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 30000},
                    "budget_tokens": {"type": "integer", "minimum": 100, "maximum": 1200},
                    "include_wake": {"type": "boolean"},
                    "rerank": {"type": "string", "enum": ["auto", "true", "false"]},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "dory_research",
            "description": "Run Dory research mode and optionally save a durable artifact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "kind": {"type": "string", "enum": ["report", "briefing", "wiki-note", "proposal"]},
                    "corpus": {"type": "string", "enum": ["durable", "sessions", "all"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "save": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        {
            "name": "dory_publish_research",
            "description": "Publish externally produced Hermes research Markdown into Dory knowledge via /v1/write. Defaults to dry_run=true; set dry_run=false to create and incrementally index knowledge/research/<timestamp>-<title>.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "question": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "target": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                    "visibility": {"type": "string", "enum": ["internal", "public", "private"], "default": "internal"},
                },
                "required": ["title", "body"],
            },
        },
        {
            "name": "dory_search",
            "description": "Search the Dory memory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "hybrid",
                            "recall",
                            "bm25",
                            "text",
                            "keyword",
                            "lexical",
                            "vector",
                            "semantic",
                            "exact",
                        ],
                    },
                    "corpus": {"type": "string", "enum": ["durable", "sessions", "all"]},
                    "scope": {
                        "type": "object",
                        "properties": {
                            "path_glob": {"type": "string"},
                            "type": {"type": "array", "items": {"type": "string"}},
                            "status": {"type": "array", "items": {"type": "string"}},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "agent": {"type": "array", "items": {"type": "string"}},
                            "device": {"type": "array", "items": {"type": "string"}},
                            "session_id": {"type": "array", "items": {"type": "string"}},
                            "session_key": {"type": "string"},
                            "since": {"type": "string"},
                            "until": {"type": "string"},
                        },
                    },
                    "include_content": {"type": "boolean"},
                    "min_relevance_score": {"type": "number"},
                    "rerank": {"type": "string", "enum": ["auto", "true", "false"]},
                    "debug": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "dory_digest",
            "description": "Fetch a daily or weekly digest recap directly by period, without relying on tags or search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["daily", "weekly"], "default": "daily"},
                    "date": {"type": "string"},
                    "week": {"type": "string"},
                    "from_line": {"type": "integer", "minimum": 1, "default": 1},
                    "lines": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 240},
                    "debug": {"type": "boolean"},
                },
            },
        },
        {
            "name": "dory_get",
            "description": "Fetch a file or line slice by path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "from": {"type": "integer"},
                    "from_line": {"type": "integer"},
                    "lines": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "dory_memory_write",
            "description": "Write semantic memory through Dory using write, replace, or forget intent. Semantic subjects can route into canonical docs; set dry_run=true to preview, allow_canonical=true to commit a canonical write, or force_inbox=true for tentative/scratch captures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "replace", "forget"]},
                    "kind": {"type": "string", "enum": ["fact", "preference", "state", "decision", "note"]},
                    "subject": {"type": "string"},
                    "content": {"type": "string"},
                    "scope": {"type": "string", "enum": ["person", "project", "concept", "decision", "core"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                    "soft": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "force_inbox": {"type": "boolean"},
                    "allow_canonical": {"type": "boolean"},
                    "agent": {"type": "string"},
                    "session_id": {"type": "string"},
                    "origin_surface": {"type": "string"},
                },
                "required": ["action", "kind", "subject", "content"],
            },
        },
        {
            "name": "dory_write",
            "description": "Exact-path markdown write. Use when you know the target path; replace/forget require expected_hash from dory_get. Set dry_run=true to validate and preview without writing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["append", "create", "replace", "forget"]},
                    "target": {"type": "string"},
                    "content": {"type": "string"},
                    "soft": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "frontmatter": {"type": "object"},
                    "agent": {"type": "string"},
                    "session_id": {"type": "string"},
                    "expected_hash": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "target"],
            },
        },
        {
            "name": "dory_purge",
            "description": "Hard-delete an exact markdown path from the corpus and index. Defaults to dry_run=true; live purge requires reason and matching expected_hash. Only scratch/generated roots are allowed unless allow_canonical=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "expected_hash": {"type": "string"},
                    "reason": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                    "allow_canonical": {"type": "boolean", "default": False},
                    "include_related_tombstone": {"type": "boolean", "default": False},
                },
                "required": ["target"],
            },
        },
        {
            "name": "dory_link",
            "description": "Inspect wikilink edges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["neighbors", "backlinks", "lint"]},
                    "path": {"type": "string"},
                    "direction": {"type": "string", "enum": ["out", "in", "both"]},
                    "depth": {"type": "integer"},
                    "max_edges": {"type": "integer", "minimum": 1, "maximum": 500, "default": 40},
                    "exclude_prefixes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["op"],
            },
        },
        {
            "name": "dory_status",
            "description": "Get Dory index and corpus status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    ]


def _build_canonical_hermes_tool_schemas() -> list[dict[str, Any]] | None:
    try:
        from dory_core.tool_registry import build_mcp_tool_schemas
    except ImportError:
        return None

    return [_mcp_tool_schema_to_hermes(tool) for tool in build_mcp_tool_schemas()]


def _mcp_tool_schema_to_hermes(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name", ""))
    parameters = deepcopy(tool.get("inputSchema"))
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    _apply_hermes_schema_defaults(name, parameters)
    return {
        "name": name,
        "description": str(tool.get("description", "")),
        "parameters": parameters,
    }


def _apply_hermes_schema_defaults(tool_name: str, parameters: dict[str, Any]) -> None:
    defaults: dict[str, dict[str, Any]] = {
        "dory_wake": {"agent": "hermes"},
        "dory_active_memory": {"agent": "hermes"},
    }
    tool_defaults = defaults.get(tool_name)
    if not tool_defaults:
        return

    properties = parameters.get("properties")
    if isinstance(properties, dict):
        for field_name, default_value in tool_defaults.items():
            field_schema = properties.get(field_name)
            if isinstance(field_schema, dict):
                field_schema["default"] = default_value

    required = parameters.get("required")
    if isinstance(required, list):
        parameters["required"] = [field for field in required if field not in tool_defaults]


def _publish_research_tool_schema() -> dict[str, Any]:
    return {
        "name": "dory_publish_research",
        "description": "Publish externally produced Hermes research Markdown into Dory knowledge via /v1/write. Defaults to dry_run=true; set dry_run=false to create and incrementally index knowledge/research/<timestamp>-<title>.md.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "question": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "target": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
                "visibility": {"type": "string", "enum": ["internal", "public", "private"], "default": "internal"},
            },
            "required": ["title", "body"],
        },
    }
