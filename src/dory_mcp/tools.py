from __future__ import annotations

from typing import Any

TOOL_MAP: dict[str, str] = {
    "dory_wake": "wake",
    "dory_search": "search",
    "dory_get": "get",
    "dory_memory_write": "memory_write",
    "dory_write": "write",
    "dory_purge": "purge",
    "dory_link": "link",
    "dory_status": "status",
    "dory_active_memory": "active_memory",
    "dory_research": "research",
}


def build_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "dory_wake",
            "description": "Build the frozen wake-up block. Use profile='coding' for agent work, 'writing' for voice/content, or 'privacy' for boundary questions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "budget_tokens": {"type": "integer"},
                    "agent": {"type": "string"},
                    "profile": {
                        "type": "string",
                        "enum": ["default", "casual", "coding", "writing", "privacy"],
                    },
                    "include_recent_sessions": {"type": "integer"},
                    "include_pinned_decisions": {"type": "boolean"},
                },
            },
        },
        {
            "name": "dory_active_memory",
            "description": "Run the bounded active-memory pre-reply pass. Limits: budget_tokens <= 1200, timeout_ms <= 5000. Set include_wake=false if wake was already called.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 5000},
                    "budget_tokens": {"type": "integer", "minimum": 100, "maximum": 1200},
                    "include_wake": {"type": "boolean"},
                },
                "required": ["prompt", "agent"],
            },
        },
        {
            "name": "dory_research",
            "description": "Run Dory research mode and save a durable artifact.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "kind": {"type": "string"},
                    "corpus": {"type": "string"},
                    "limit": {"type": "integer"},
                    "save": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        {
            "name": "dory_search",
            "description": "Search the memory tree.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "bm25",
                            "text",
                            "keyword",
                            "lexical",
                            "vector",
                            "semantic",
                            "hybrid",
                            "recall",
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
                            "since": {"type": "string"},
                            "until": {"type": "string"},
                        },
                    },
                    "include_content": {"type": "boolean"},
                    "min_score": {"type": "number"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "dory_get",
            "description": "Fetch a file or slice by path.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "from": {"type": "integer"},
                    "lines": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "dory_memory_write",
            "description": "Write semantic memory through Dory using write, replace, or forget intent. Semantic subjects can route into canonical docs; set dry_run=true to preview, allow_canonical=true to commit a canonical write, or force_inbox=true for tentative/scratch captures.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "replace", "forget"]},
                    "kind": {"type": "string"},
                    "subject": {"type": "string"},
                    "content": {"type": "string"},
                    "scope": {"type": "string"},
                    "confidence": {"type": "string"},
                    "source": {"type": "string"},
                    "soft": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "force_inbox": {"type": "boolean"},
                    "allow_canonical": {"type": "boolean"},
                    "agent": {"type": "string"},
                    "session_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["action", "kind", "subject", "content"],
            },
        },
        {
            "name": "dory_write",
            "description": "Exact-path markdown write. Use when you know the target path; replace/forget require expected_hash from dory_get. Set dry_run=true to validate and preview without writing.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
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
            "inputSchema": {
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
            "inputSchema": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "path": {"type": "string"},
                    "direction": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["op"],
            },
        },
        {
            "name": "dory_status",
            "description": "Get Dory index and corpus status.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
