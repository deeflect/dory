"""Single source of truth for the Dory tool surface.

MCP (`dory_mcp.tools`), the HTTP→MCP bridge (`scripts/claude-code/dory-mcp-http-bridge.py`),
and any future adapter should read from this registry instead of hand-rolling tool
schemas. Input schemas are generated from the Pydantic request models in
`dory_core.types`, so adding a field to a request model automatically updates every
surface that advertises the tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from dory_core.types import (
    ActiveMemoryReq,
    LinkReq,
    MemoryWriteReq,
    PurgeReq,
    ResearchReq,
    SearchReq,
    WakeReq,
    WriteReq,
)


HttpMethod = Literal["GET", "POST"]


@dataclass(frozen=True, slots=True)
class DoryTool:
    name: str
    http_method: HttpMethod
    http_path: str
    description: str
    # When present, the MCP input schema is generated from this Pydantic model.
    # When None, `input_schema_override` must be supplied.
    request_model: type[BaseModel] | None = None
    input_schema_override: dict[str, Any] | None = None
    # Short key used by dory_mcp.server to route to its internal handler.
    handler_key: str = ""


_GET_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "from": {"type": "integer"},
        "from_line": {"type": "integer"},
        "lines": {"type": "integer"},
    },
    "required": ["path"],
}

_STATUS_INPUT_SCHEMA = {"type": "object", "properties": {}}


TOOL_REGISTRY: tuple[DoryTool, ...] = (
    DoryTool(
        name="dory_wake",
        http_method="POST",
        http_path="/v1/wake",
        description=(
            "Build the frozen wake-up block. Use profile='coding' for agent work, "
            "'writing' for voice/content, or 'privacy' for boundary questions."
        ),
        request_model=WakeReq,
        handler_key="wake",
    ),
    DoryTool(
        name="dory_active_memory",
        http_method="POST",
        http_path="/v1/active-memory",
        description=(
            "Run the bounded active-memory pre-reply pass. Limits: budget_tokens <= 1200, "
            "timeout_ms <= 5000. Set include_wake=false if wake was already called."
        ),
        request_model=ActiveMemoryReq,
        handler_key="active_memory",
    ),
    DoryTool(
        name="dory_research",
        http_method="POST",
        http_path="/v1/research",
        description="Run Dory research mode and save a durable artifact.",
        request_model=ResearchReq,
        handler_key="research",
    ),
    DoryTool(
        name="dory_search",
        http_method="POST",
        http_path="/v1/search",
        description="Search the memory tree.",
        request_model=SearchReq,
        handler_key="search",
    ),
    DoryTool(
        name="dory_get",
        http_method="GET",
        http_path="/v1/get",
        description="Fetch a file or slice by path.",
        input_schema_override=_GET_INPUT_SCHEMA,
        handler_key="get",
    ),
    DoryTool(
        name="dory_memory_write",
        http_method="POST",
        http_path="/v1/memory-write",
        description=(
            "Write semantic memory through Dory using write, replace, or forget intent. "
            "Semantic subjects can route into canonical docs; set dry_run=true to preview, "
            "allow_canonical=true to commit a canonical write, or force_inbox=true for "
            "tentative/scratch captures."
        ),
        request_model=MemoryWriteReq,
        handler_key="memory_write",
    ),
    DoryTool(
        name="dory_write",
        http_method="POST",
        http_path="/v1/write",
        description=(
            "Exact-path markdown write. kind is append|create|replace|forget. "
            "Creating a new file requires frontmatter.title and frontmatter.type; inbox paths "
            "should use type='capture' and note pages should live under references/notes/ with "
            "type='note'. replace/forget require expected_hash from dory_get; forget also "
            "requires reason. Set dry_run=true to validate and preview without writing."
        ),
        request_model=WriteReq,
        handler_key="write",
    ),
    DoryTool(
        name="dory_purge",
        http_method="POST",
        http_path="/v1/purge",
        description=(
            "Hard-delete an exact markdown path from the corpus and index. Defaults to "
            "dry_run=true; live purge requires reason and matching expected_hash. Only "
            "scratch/generated roots are allowed unless allow_canonical=true."
        ),
        request_model=PurgeReq,
        handler_key="purge",
    ),
    DoryTool(
        name="dory_link",
        http_method="POST",
        http_path="/v1/link",
        description="Inspect wikilink edges.",
        request_model=LinkReq,
        handler_key="link",
    ),
    DoryTool(
        name="dory_status",
        http_method="GET",
        http_path="/v1/status",
        description="Get Dory index and corpus status.",
        input_schema_override=_STATUS_INPUT_SCHEMA,
        handler_key="status",
    ),
)


TOOL_MAP: dict[str, str] = {tool.name: tool.handler_key for tool in TOOL_REGISTRY}


def tool_by_name(name: str) -> DoryTool | None:
    for tool in TOOL_REGISTRY:
        if tool.name == name:
            return tool
    return None


def build_mcp_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": _input_schema_for(tool),
        }
        for tool in TOOL_REGISTRY
    ]


def _input_schema_for(tool: DoryTool) -> dict[str, Any]:
    if tool.input_schema_override is not None:
        return tool.input_schema_override
    if tool.request_model is None:
        raise RuntimeError(f"tool {tool.name!r} has neither request_model nor input_schema_override")
    return _pydantic_to_mcp_schema(tool.request_model)


def _pydantic_to_mcp_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {}) or {}
    inlined = _inline_refs(raw, defs)
    return _strip_cruft(inlined)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/$defs/"):
            ref_name = node["$ref"].removeprefix("#/$defs/")
            target = defs.get(ref_name)
            if target is not None:
                return _inline_refs(target, defs)
        return {key: _inline_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


_CRUFT_KEYS = frozenset({"title"})


def _strip_cruft(node: Any) -> Any:
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in _CRUFT_KEYS:
                continue
            cleaned[key] = _strip_cruft(value)
        # Collapse `anyOf: [{type: X}, {type: null}]` (Optional[X]) to just {type: X}
        # for cleaner MCP advertisement — null is still accepted by Pydantic at parse.
        if set(cleaned.keys()) == {"anyOf", "default"} or set(cleaned.keys()) == {"anyOf"}:
            any_of = cleaned.get("anyOf")
            if isinstance(any_of, list) and len(any_of) == 2:
                non_null = [item for item in any_of if item.get("type") != "null"]
                if len(non_null) == 1:
                    result = dict(non_null[0])
                    if "default" in cleaned:
                        result["default"] = cleaned["default"]
                    return result
        return cleaned
    if isinstance(node, list):
        return [_strip_cruft(item) for item in node]
    return node
