"""Seed the ``core/`` bucket from a source root's canonical brain files.

Typical source layouts have `SOUL.md`, `USER.md`, `IDENTITY.md`,
`AGENTS.md`, `TOOLS.md`, `MEMORY.md`, etc. at the root level — outside
the memory/ directory because they describe the brain itself, not the
ingested content. We pull any uppercase-stem markdown file at the
source root into ``core/`` with a lowercased stem, synthesize the
minimal canonical frontmatter, and mark them ``canonical=true``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from dory_core.frontmatter import (
    dump_markdown_document,
    load_markdown_document,
    merge_frontmatter,
)
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.metadata import normalize_frontmatter


_DEFAULT_CORE_FRONTMATTER = {
    "type": "core",
    "status": "active",
    "canonical": True,
    "source_kind": "canonical",
    "temperature": "hot",
}

# Dory's canonical core set. Brain-file imports that match one of these
# stems after lowercasing land under core/. Anything else gets skipped
# and should be routed to references/ops or knowledge/ manually.
_DORY_CORE_STEMS: frozenset[str] = frozenset(
    {"user", "soul", "identity", "env", "active", "defaults"}
)


@dataclass(frozen=True, slots=True)
class CoreSeedResult:
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def seed_core_from_root(
    core_source: Path,
    corpus_root: Path,
    *,
    dry_run: bool = False,
) -> CoreSeedResult:
    """Copy every UPPERCASE-stem ``*.md`` at ``core_source`` into ``corpus/core/``.

    Files like ``SOUL.md`` become ``core/soul.md``. Frontmatter is
    synthesized where missing and normalized via the standard pipeline.
    """
    copied: list[str] = []
    skipped: list[str] = []

    if not core_source.exists() or not core_source.is_dir():
        return CoreSeedResult(copied=copied, skipped=[str(core_source)])

    for path in sorted(core_source.glob("*.md")):
        if not _is_core_candidate(path):
            skipped.append(path.name)
            continue
        destination = Path("core") / f"{path.stem.lower()}.md"
        try:
            rendered = _render_core_document(path, destination)
        except Exception:
            skipped.append(path.name)
            continue
        if dry_run:
            copied.append(destination.as_posix())
            continue
        target = resolve_corpus_target(corpus_root, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, rendered, encoding="utf-8")
        copied.append(destination.as_posix())

    return CoreSeedResult(copied=copied, skipped=skipped)


def _is_core_candidate(path: Path) -> bool:
    """Accept only Dory-standard uppercase-stem brain files.

    The source convention is SCREAMING_CAPS (SOUL.md, USER.md, etc.). Only
    files whose lowercased stem is in the Dory canonical core set make it
    to ``core/`` — the rest (AGENTS.md, TOOLS.md, MEMORY.md, DREAMS.md,
    HEARTBEAT.md) belong in ``references/ops/`` or ``knowledge/``.
    """
    stem = path.stem
    if not stem:
        return False
    letters = [c for c in stem if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return False
    return stem.lower() in _DORY_CORE_STEMS


def _render_core_document(source: Path, destination: Path) -> str:
    text = source.read_text(encoding="utf-8")
    try:
        document = load_markdown_document(text)
        body = document.body
        raw = dict(document.frontmatter)
    except ValueError:
        raw = {}
        body = text

    title = _infer_title(raw, body, fallback=source.stem)
    merged = merge_frontmatter(raw, {**_DEFAULT_CORE_FRONTMATTER, "title": title})
    merged.setdefault("created", _today())
    normalized = normalize_frontmatter(merged, target=destination)
    return dump_markdown_document(normalized, body)


def _infer_title(
    frontmatter: dict[str, object], body: str, *, fallback: str
) -> str:
    existing = frontmatter.get("title")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback.replace("_", " ").replace("-", " ").title()


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def format_seed_summary(result: CoreSeedResult) -> dict[str, object]:
    return {
        "copied": result.copied,
        "skipped": result.skipped,
        "copied_count": len(result.copied),
        "skipped_count": len(result.skipped),
    }
