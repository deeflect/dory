from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueryProfile:
    tokens: tuple[str, ...]
    phrases: tuple[str, ...]
    has_identifier_hint: bool
    has_temporal_hint: bool


@dataclass(frozen=True, slots=True)
class _ChunkRow:
    chunk_id: str
    path: str
    content: str
    start_line: int
    end_line: int
    frontmatter_json: str
    score: float
