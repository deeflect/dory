from __future__ import annotations

from difflib import SequenceMatcher
from typing import Sequence

from dory_core.search.types import _ChunkRow
from dory_core.search.utils import _searchable_body


def _is_retired_document(frontmatter: dict[str, object]) -> bool:
    status = str(frontmatter.get("status", "")).lower()
    if status in {"superseded", "retired"}:
        return True
    superseded_by = frontmatter.get("superseded_by")
    return isinstance(superseded_by, str) and bool(superseded_by.strip())


def _is_low_trust_search_document(path: str, frontmatter: dict[str, object]) -> bool:
    if path.startswith("inbox/quarantine/") or path.endswith(".tombstone.md"):
        return True
    if frontmatter.get("migration_quarantined") is True:
        return True
    status = str(frontmatter.get("status", "")).strip().lower()
    return status in {"quarantined", "quarantine"}


def _collapse_duplicate_documents(
    rows: Sequence[tuple[_ChunkRow, dict[str, object]]],
) -> list[tuple[_ChunkRow, dict[str, object]]]:
    collapsed: list[tuple[_ChunkRow, dict[str, object]]] = []
    for row, frontmatter in rows:
        duplicate_index = next(
            (
                index
                for index, (existing_row, _existing_frontmatter) in enumerate(collapsed)
                if _documents_are_near_duplicates(existing_row, row)
            ),
            None,
        )
        if duplicate_index is None:
            collapsed.append((row, frontmatter))
            continue
        existing_row, existing_frontmatter = collapsed[duplicate_index]
        if _document_precedence(row, frontmatter) > _document_precedence(existing_row, existing_frontmatter):
            collapsed[duplicate_index] = (row, frontmatter)
    return collapsed


def _documents_are_near_duplicates(left: _ChunkRow, right: _ChunkRow) -> bool:
    if left.path == right.path:
        return True
    left_text = _duplicate_signature_text(left.content)
    right_text = _duplicate_signature_text(right.content)
    if len(left_text) < 80 or len(right_text) < 80:
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if shorter in longer and len(shorter) / len(longer) >= 0.7:
        return True
    return SequenceMatcher(None, left_text, right_text).ratio() >= 0.9


def _duplicate_signature_text(content: str) -> str:
    body = _searchable_body(content).casefold()
    lines = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return " ".join(" ".join(lines).split())


def _document_precedence(row: _ChunkRow, frontmatter: dict[str, object]) -> tuple[int, float, str]:
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    status = str(frontmatter.get("status", "")).strip().lower()
    score = 0
    if frontmatter.get("canonical") is True:
        score += 100
    if source_kind == "canonical":
        score += 80
    if row.path.startswith("core/"):
        score += 50
    if row.path.startswith("projects/") and row.path.endswith("/state.md"):
        score += 45
    if status == "active":
        score += 10
    if row.path.startswith("wiki/"):
        score -= 40
    if row.path.startswith("sources/semantic/"):
        score -= 35
    if row.path.startswith("inbox/"):
        score -= 25
    if row.path.startswith("logs/"):
        score -= 50
    if source_kind == "generated":
        score -= 20
    return score, row.score, row.path
