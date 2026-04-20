"""Execute a source-router manifest against a Dory corpus.

This is the path-migration layer: it reads source files, applies
frontmatter hygiene (fill bare files), applies archive-tombstone marks
for files routed to ``archive/``, and writes the transformed markdown
to the target corpus. Content-level work — claim extraction, concept
synthesis, entity resolution — is handled separately by the
LLM-backed migration engine or the digest-mining pass.

The executor is intentionally dumb: given a ``RoutingDecision`` with
``kind="route"``, it moves the file. It never classifies, never asks
an LLM, never deletes the source. Dry-run mode returns the same
report without writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

from dory_core.frontmatter import (
    dump_markdown_document,
    load_markdown_document,
    merge_frontmatter,
)
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.metadata import (
    normalize_frontmatter,
)
from dory_core.migration_source_router import RoutingDecision, walk_source_tree
from dory_core.slug import slugify_path_segment


ProgressCallback = Callable[["ExecutionProgress"], None]


@dataclass(frozen=True, slots=True)
class ExecutionProgress:
    index: int  # 1-based index of file just processed
    total: int
    written: int
    skipped: int
    errored: int
    last_action: str  # "written" | "skipped" | "error"
    last_destination: str  # corpus-relative path or ""
    last_source: str  # source path (for context)


_ARCHIVE_TOMBSTONE = {
    "canonical": False,
    "status": "superseded",
    "source_kind": "legacy",
    "temperature": "cold",
}


@dataclass(frozen=True, slots=True)
class ExecutedEntry:
    source_path: str
    destination: str
    action: str  # "written" | "skipped" | "error"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    total_decisions: int
    routed: int
    excluded: int
    reviewed: int
    written: int
    skipped: int
    errored: int
    entries: list[ExecutedEntry] = field(default_factory=list)


def execute_manifest(
    decisions: Iterable[RoutingDecision],
    *,
    source_root: Path,
    corpus_root: Path,
    dry_run: bool = False,
    include_review: bool = False,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> ExecutionReport:
    """Execute a sequence of routing decisions against ``corpus_root``.

    Only ``kind="route"`` decisions produce writes. Review decisions
    are skipped unless ``include_review`` is set, and even then they
    need a destination to be actionable (reviews with no destination
    are always skipped).

    If ``progress`` is provided it is called once per decision with an
    ``ExecutionProgress`` snapshot.
    """
    decisions_list = list(decisions)
    if limit is not None:
        decisions_list = decisions_list[:limit]

    total = len(decisions_list)
    routed = sum(1 for d in decisions_list if d.kind == "route")
    excluded = sum(1 for d in decisions_list if d.kind == "exclude")
    reviewed = sum(1 for d in decisions_list if d.kind == "review")

    entries: list[ExecutedEntry] = []
    written = 0
    skipped = 0
    errored = 0

    def _emit(index: int, action: str, destination: str, source: str) -> None:
        if progress is None:
            return
        progress(
            ExecutionProgress(
                index=index,
                total=total,
                written=written,
                skipped=skipped,
                errored=errored,
                last_action=action,
                last_destination=destination,
                last_source=source,
            )
        )

    for index, decision in enumerate(decisions_list, start=1):
        if decision.kind == "exclude":
            entries.append(
                ExecutedEntry(
                    source_path=str(decision.source_path),
                    destination="",
                    action="skipped",
                    reason=decision.reason,
                )
            )
            skipped += 1
            _emit(index, "skipped", "", str(decision.source_path))
            continue

        if decision.kind == "review" and not include_review:
            entries.append(
                ExecutedEntry(
                    source_path=str(decision.source_path),
                    destination="",
                    action="skipped",
                    reason=f"review case: {decision.reason}",
                )
            )
            skipped += 1
            _emit(index, "skipped", "", str(decision.source_path))
            continue

        if decision.destination is None:
            entries.append(
                ExecutedEntry(
                    source_path=str(decision.source_path),
                    destination="",
                    action="skipped",
                    reason="no destination",
                )
            )
            skipped += 1
            _emit(index, "skipped", "", str(decision.source_path))
            continue

        try:
            rendered = _render_migrated_document(decision, source_root=source_root)
        except Exception as err:  # pragma: no cover - defensive catch
            entries.append(
                ExecutedEntry(
                    source_path=str(decision.source_path),
                    destination=decision.destination.as_posix(),
                    action="error",
                    reason=f"{type(err).__name__}: {err}",
                )
            )
            errored += 1
            _emit(
                index,
                "error",
                decision.destination.as_posix(),
                str(decision.source_path),
            )
            continue

        if dry_run:
            entries.append(
                ExecutedEntry(
                    source_path=str(decision.source_path),
                    destination=decision.destination.as_posix(),
                    action="written",
                    reason="dry-run",
                )
            )
            written += 1
            _emit(
                index,
                "written",
                decision.destination.as_posix(),
                str(decision.source_path),
            )
            continue

        target = resolve_corpus_target(corpus_root, decision.destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, rendered, encoding="utf-8")
        entries.append(
            ExecutedEntry(
                source_path=str(decision.source_path),
                destination=decision.destination.as_posix(),
                action="written",
            )
        )
        written += 1
        _emit(
            index,
            "written",
            decision.destination.as_posix(),
            str(decision.source_path),
        )

    return ExecutionReport(
        total_decisions=total,
        routed=routed,
        excluded=excluded,
        reviewed=reviewed,
        written=written,
        skipped=skipped,
        errored=errored,
        entries=entries,
    )


def execute_source_tree(
    source_root: Path,
    corpus_root: Path,
    *,
    dry_run: bool = False,
    include_review: bool = False,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> ExecutionReport:
    """Convenience: walk the source tree and execute the resulting manifest."""
    decisions = walk_source_tree(source_root)
    return execute_manifest(
        decisions,
        source_root=source_root,
        corpus_root=corpus_root,
        dry_run=dry_run,
        include_review=include_review,
        limit=limit,
        progress=progress,
    )


def _render_migrated_document(
    decision: RoutingDecision,
    *,
    source_root: Path,
) -> str:
    """Load the source file, apply hygiene + archive marks, return rendered markdown."""
    assert decision.destination is not None
    source_text = decision.source_path.read_text(encoding="utf-8")

    try:
        parsed = load_markdown_document(source_text)
        body = parsed.body
        raw_frontmatter = dict(parsed.frontmatter)
    except ValueError:
        # Bare file — synthesize minimal frontmatter from filename and mtime.
        raw_frontmatter = _synthesize_frontmatter(decision)
        body = source_text

    enriched = _apply_destination_defaults(raw_frontmatter, decision.destination)

    if _is_archive_destination(decision.destination):
        enriched = merge_frontmatter(enriched, _ARCHIVE_TOMBSTONE)

    try:
        normalized = normalize_frontmatter(enriched, target=decision.destination)
    except Exception:
        # If strict normalize rejects, fall back to the enriched dict with
        # minimum-required keys filled. This preserves the source content
        # without losing it to a hard validation error.
        normalized = _fallback_normalize(enriched, decision)

    return dump_markdown_document(normalized, body)


def _synthesize_frontmatter(decision: RoutingDecision) -> dict[str, object]:
    """Build minimal frontmatter for source files that have none."""
    stem = decision.source_path.stem
    title = stem.replace("-", " ").replace("_", " ").strip() or "Untitled"
    title = " ".join(word.capitalize() if word.islower() else word for word in title.split())
    try:
        mtime = datetime.fromtimestamp(decision.source_path.stat().st_mtime, tz=UTC)
        created = mtime.date().isoformat()
    except OSError:
        created = datetime.now(tz=UTC).date().isoformat()
    return {
        "title": title,
        "type": "capture",
        "status": "raw",
        "created": created,
    }


def _apply_destination_defaults(
    frontmatter: dict[str, object],
    destination: Path,
) -> dict[str, object]:
    """Ensure title and type are present, inferring from destination when missing."""
    enriched = dict(frontmatter)
    if not enriched.get("title"):
        enriched["title"] = destination.stem.replace("-", " ").replace("_", " ").title()
    if "type" not in enriched or not str(enriched.get("type", "")).strip():
        inferred = _type_from_destination(destination)
        if inferred is not None:
            enriched["type"] = inferred
    return enriched


def _type_from_destination(destination: Path) -> str | None:
    """Infer a doc type from the destination path's top bucket."""
    parts = destination.parts
    if not parts:
        return None
    top = parts[0]
    if top == "logs" and len(parts) > 1:
        if parts[1] == "daily":
            return "daily"
        if parts[1] == "weekly":
            return "weekly"
        if parts[1] == "sessions":
            return "session"
    if top == "digests" and len(parts) > 1:
        if parts[1] == "daily":
            return "digest-daily"
        if parts[1] == "weekly":
            return "digest-weekly"
    if top == "projects":
        return "project"
    if top == "people":
        return "person"
    if top == "decisions":
        return "decision"
    if top == "concepts":
        return "concept"
    if top == "ideas":
        return "idea"
    if top == "knowledge":
        return "knowledge"
    if top == "references" and len(parts) > 1:
        if parts[1] == "reports":
            return "report"
        if parts[1] == "briefings":
            return "briefing"
        if parts[1] == "slides":
            return "slide"
        if parts[1] == "notes":
            return "note"
        if parts[1] == "tweets":
            return "reference"
        return "reference"
    if top == "inbox":
        return "capture"
    if top == "wiki":
        return "wiki"
    if top == "archive" and len(parts) > 1:
        inner = parts[1]
        if inner == "daily":
            return "daily"
        if inner == "weekly":
            return "weekly"
        if inner == "sessions":
            return "session"
        if inner == "projects":
            return "project"
        if inner == "ideas":
            return "idea"
        if inner == "knowledge":
            return "knowledge"
    return None


def _is_archive_destination(destination: Path) -> bool:
    return destination.parts[:1] == ("archive",)


def _fallback_normalize(
    frontmatter: dict[str, object],
    decision: RoutingDecision,
) -> dict[str, object]:
    """Produce a best-effort frontmatter for files that fail strict validation.

    This only runs when normalize_frontmatter raises — usually because
    of a field we couldn't coerce. We fill the bare minimum required
    by downstream tools (title, type, status, created) and mark the
    document as raw so the user can hand-review later.
    """
    enriched = dict(frontmatter)
    enriched.setdefault("title", decision.source_path.stem or "Untitled")
    enriched["type"] = "capture"
    enriched["status"] = "raw"
    enriched.setdefault("source_kind", "legacy")
    enriched.setdefault("canonical", False)
    enriched.setdefault("temperature", "cold")
    enriched.setdefault(
        "created",
        datetime.now(tz=UTC).date().isoformat(),
    )
    enriched["migration_quarantined"] = True
    return enriched
