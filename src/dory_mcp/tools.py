"""MCP tool descriptors.

The canonical source of truth is `dory_core.tool_registry`; this module just
re-exports the MCP-facing adapter so legacy imports keep working.
"""

from __future__ import annotations

from dory_core.tool_registry import TOOL_MAP, build_mcp_tool_schemas as build_tool_schemas


__all__ = ["TOOL_MAP", "build_tool_schemas"]
