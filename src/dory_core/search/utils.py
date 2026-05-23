from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Sequence

from dory_core.frontmatter import load_markdown_document
from dory_core.llm_rerank import RerankCandidate
from dory_core.schema import TIMELINE_MARKER

_TIMELINE_ENTRY_RE = re.compile(r"(?m)^\s*-\s*(\d{4}-\d{2}-\d{2}):")
_STALE_GRACE_DAYS = 7


def _load_frontmatter(payload: str) -> dict[str, object]:
    if not payload:
        return {}
    return json.loads(payload)


def _searchable_body(content: str) -> str:
    if not content:
        return ""
    try:
        return load_markdown_document(content).body.strip()
    except ValueError:
        return content.strip()


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _extract_document_date(frontmatter: dict[str, object]) -> date | None:
    for key in ("date", "updated", "created"):
        value = frontmatter.get(key)
        if not isinstance(value, str):
            continue
        candidate = value[:10]
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _extract_reference_date(frontmatter: dict[str, object]) -> date | None:
    for key in ("updated", "created", "date"):
        value = frontmatter.get(key)
        if not isinstance(value, str):
            continue
        candidate = value[:10]
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _build_stale_warning(content: str, frontmatter: dict[str, object]) -> str | None:
    if TIMELINE_MARKER not in content:
        return None
    reference_date = _extract_reference_date(frontmatter)
    if reference_date is None:
        return None
    _, _, timeline = content.partition(TIMELINE_MARKER)
    timeline_dates = [date.fromisoformat(match.group(1)) for match in _TIMELINE_ENTRY_RE.finditer(timeline)]
    if not timeline_dates:
        return None
    latest_timeline_date = max(timeline_dates)
    if latest_timeline_date <= reference_date + timedelta(days=_STALE_GRACE_DAYS):
        return None
    return f"compiled truth may be outdated (last timeline entry: {latest_timeline_date.isoformat()})"


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _rerank_candidate_from_row(row: object) -> RerankCandidate:
    """Build a RerankCandidate from a chunk row for the reranker."""
    from dory_core.search.types import _ChunkRow

    if not isinstance(row, _ChunkRow):
        # Fall back to attribute access for duck-typed rows
        return RerankCandidate(
            chunk_id=str(getattr(row, "chunk_id", "")),
            path=str(getattr(row, "path", "")),
            title="",
            snippet=str(getattr(row, "content", "")),
            frontmatter_hints={},
        )
    frontmatter = _load_frontmatter(row.frontmatter_json)
    hints: dict[str, str] = {}
    for key in ("type", "status", "canonical", "source_kind", "temperature", "date", "updated"):
        value = frontmatter.get(key)
        if value is None:
            continue
        hints[key] = str(value)
    title = frontmatter.get("title")
    return RerankCandidate(
        chunk_id=row.chunk_id,
        path=row.path,
        title=str(title) if isinstance(title, str) else "",
        snippet=_searchable_body(row.content),
        frontmatter_hints=hints,
    )


def _normalized_filter_values(values: Sequence[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]
