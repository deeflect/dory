"""Shared hot context packet for wake and active-memory.

Provides the typed ``HotContextPacket`` dataclass and the
``SourceBackedItem`` helper used to unify wake and active-memory
around one internal packet.

Usage (internal):
    packet = build_hot_context_packet(
        profile="coding",
        guardrails=("no personal data",),
        project=entity_ctx,
        ...
    )
    block = render_packet_to_block(packet, budget_tokens=600)

External ``WakeResp`` and ``ActiveMemoryResp`` remain unchanged until
a deliberate API-review pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dory_core.markdown_excerpt import result_evidence_text, truncate_text

if TYPE_CHECKING:
    from dory_core.entity_context import EntityContext

_MEMORY_BULLET_CHARS = 220
_RENDER_SNIPPET_CHARS = 300
_SESSION_RENDER_SNIPPET_CHARS = 160


@dataclass(frozen=True, slots=True)
class SourceBackedItem:
    """A single piece of context with an optional source path.

    Used inside ``HotContextPacket`` for every category of evidence.
    The *text* is the human-readable content; *source_path* is the
    relative markdown path it was derived from.
    """

    text: str
    """Human-readable content, possibly truncated for budget."""

    source_path: str | None = None
    """Relative path to the source markdown file, if known."""


@dataclass(frozen=True, slots=True)
class HotContextPacket:
    """Typed internal packet unifying wake and active-memory context.

    Every field is a tuple (immutable, hashable) for safe sharing.
    The packet is assembled before rendering and passed through the
    rendering pipeline instead of separate loose lists.

    External response types (``WakeResp``, ``ActiveMemoryResp``) are
    produced *from* this packet; they are not replaced by it.
    """

    profile: str
    """Active profile name (e.g. ``coding``, ``privacy``, ``default``)."""

    guardrails: tuple[str, ...]
    """Active guardrails or boundary hints for the LLM consumer."""

    project: EntityContext | None
    """Resolved project entity context, if deterministically known."""

    entity_context: tuple[EntityContext, ...]
    """Resolved entity contexts beyond the primary project."""

    active_claims: tuple[SourceBackedItem, ...]
    """Active claims — short, high-signal truth statements."""

    observations: tuple[SourceBackedItem, ...]
    """Derived observations over evidence."""

    durable_evidence: tuple[SourceBackedItem, ...]
    """Evidence from durable (indexed) memory."""

    session_evidence: tuple[SourceBackedItem, ...]
    """Evidence from the session plane."""

    sources: tuple[str, ...]
    """Deduplicated source paths backing this packet."""

    warnings: tuple[str, ...]
    """Warnings about partial data, errors, or low confidence."""

    partial: bool
    """True when the packet was assembled with degraded data."""


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def source_backed_items_from_results(
    results: list[object],
    *,
    max_items: int = 4,
    snippet_chars: int = _RENDER_SNIPPET_CHARS,
    root: Path | None = None,
) -> tuple[SourceBackedItem, ...]:
    """Convert search result objects to a tuple of ``SourceBackedItem``.

    Respects the *max_items* limit and truncates each snippet to
    *snippet_chars* via the existing ``result_evidence_text`` helper.
    """
    items: list[SourceBackedItem] = []
    for result in results[:max_items]:
        path = str(getattr(result, "path", "") or "")
        if not path:
            continue
        snippet = result_evidence_text(result, root=root)
        if not snippet:
            continue
        items.append(
            SourceBackedItem(
                text=truncate_text(snippet, snippet_chars),
                source_path=path,
            )
        )
    return tuple(items)


def dedupe_sources(*source_lists: list[str]) -> tuple[str, ...]:
    """Merge and deduplicate source path lists, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for src_list in source_lists:
        for path in src_list:
            key = path.strip()
            if key and key not in seen:
                seen.add(key)
                result.append(key)
    return tuple(result)


# ---------------------------------------------------------------------------
# Rendering helpers (internal, for producing the final block)
# ---------------------------------------------------------------------------


def render_packet_to_block(
    packet: HotContextPacket,
    *,
    budget_tokens: int,
    include_active_claims: bool = True,
    include_observations: bool = False,
    include_durable: bool = True,
    include_session: bool = True,
) -> str:
    """Render a ``HotContextPacket`` to a markdown block string.

    This is the canonical rendering path for the packet.  It replaces
    the previous pattern of passing separate results lists through
    ``build_block`` / ``render_active_memory_section`` while keeping
    the same section structure.
    """
    sections: list[str] = []

    # --- Active claims / memory ---
    items = (
        list(packet.active_claims)
        + list(packet.observations)
    )
    if items and include_active_claims:
        bullets = [f"- {item.text}" for item in items[:5]]
        if bullets:
            sections.append("## Active memory\n" + "\n".join(bullets))

    # --- Durable evidence ---
    if packet.durable_evidence and include_durable:
        lines = ["## Durable evidence"]
        for item in packet.durable_evidence[:3]:
            lines.append(f"- {item.source_path or '(unknown)'}")
            if item.text:
                lines.append(f"  {item.text}")
        sections.append("\n".join(lines))

    # --- Session evidence ---
    if packet.session_evidence and include_session:
        lines = ["## Session evidence"]
        for item in packet.session_evidence[:2]:
            lines.append(f"- {item.source_path or '(unknown)'}")
            if item.text:
                lines.append(f"  {item.text}")
        sections.append("\n".join(lines))

    block = "\n\n".join(sections).strip()
    return _fit_to_budget(block, budget_tokens=budget_tokens)


def render_packet_summary(packet: HotContextPacket) -> str:
    """Produce a one-line summary from the packet."""
    candidates: list[str] = []
    for item in packet.active_claims[:1]:
        candidates.append(item.text)
    for item in packet.observations[:1]:
        candidates.append(item.text)
    for item in packet.durable_evidence[:1]:
        candidates.append(item.text)
    for item in packet.session_evidence[:1]:
        candidates.append(item.text)
    if candidates:
        return " | ".join(candidates)[:280]
    return ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fit_to_budget(block: str, *, budget_tokens: int) -> str:
    """Trim block to fit within budget, matching ``fit_block_to_budget``."""
    _CHARS_PER_TOKEN = 3  # placeholder; actual value varies
    _MAX_BLOCK_CHARS = 3200
    _MIN_BLOCK_CHARS = 700
    char_limit = min(_MAX_BLOCK_CHARS, max(_MIN_BLOCK_CHARS, budget_tokens * _CHARS_PER_TOKEN))
    if len(block) <= char_limit:
        return block
    truncated = block[: max(0, char_limit - 1)].rstrip()
    if "\n## " in truncated:
        section_safe = truncated.rsplit("\n## ", 1)[0].rstrip()
        if len(section_safe) >= _MIN_BLOCK_CHARS:
            return section_safe
    return truncated + "…"
