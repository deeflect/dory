"""Deterministic path router for legacy-memory migration sources.

Given a source path under a legacy memory root (like
``/legacy/memory``), decide where the file should land in
the canonical Dory corpus structure. Content-level decisions
(claim extraction, concept promotion, dedup winner) stay with the LLM
layer; this module only routes on path + filename shape.

The router returns a ``RoutingDecision`` that is one of:
- ``ROUTE`` with a destination relative to the Dory corpus root
- ``EXCLUDE`` for system/operational files that do not belong in the corpus
- ``REVIEW`` for cases that need LLM routing (ambiguous supporting files,
  cross-bucket potential duplicates)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dory_core.slug import slugify_path_segment


RoutingKind = Literal["route", "exclude", "review"]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    kind: RoutingKind
    source_path: Path
    destination: Path | None = None
    reason: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def route(
        cls,
        source_path: Path,
        destination: Path,
        *,
        reason: str = "",
        tags: tuple[str, ...] = (),
    ) -> "RoutingDecision":
        return cls(
            kind="route",
            source_path=source_path,
            destination=destination,
            reason=reason,
            tags=tags,
        )

    @classmethod
    def exclude(cls, source_path: Path, *, reason: str) -> "RoutingDecision":
        return cls(kind="exclude", source_path=source_path, reason=reason)

    @classmethod
    def review(
        cls,
        source_path: Path,
        *,
        reason: str,
        tags: tuple[str, ...] = (),
    ) -> "RoutingDecision":
        return cls(kind="review", source_path=source_path, reason=reason, tags=tags)


_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_WEEK_RE = re.compile(r"\b(\d{4})-W(\d{2})\b")
_DIGEST_SUFFIX = "-digest"

_EXCLUDED_ROOTS = {"system", "media", ".dreams", ".stfolder"}
_PRESERVE_ROOTS = {"archive"}


def route_source_path(source_path: Path, *, source_root: Path) -> RoutingDecision:
    """Return a routing decision for a single source file.

    ``source_path`` must be absolute. ``source_root`` is the legacy-memory
    root (e.g. ``/legacy/memory``). The returned destination is
    always relative to the Dory corpus root.
    """
    try:
        relative = source_path.resolve().relative_to(source_root.resolve())
    except ValueError:
        return RoutingDecision.exclude(
            source_path, reason="source_path is not under source_root"
        )

    if source_path.suffix.lower() != ".md":
        return RoutingDecision.exclude(source_path, reason="non-markdown file")

    parts = relative.parts
    if not parts:
        return RoutingDecision.exclude(source_path, reason="empty relative path")

    top = parts[0]

    if top in _EXCLUDED_ROOTS:
        return RoutingDecision.exclude(
            source_path, reason=f"excluded top-level directory: {top}/"
        )

    if top == "ops":
        return _route_ops(source_path, relative)

    if top == "reports":
        return _route_reports(source_path, relative)

    if top == "inbox":
        return _route_inbox(source_path, relative)

    if top == "active":
        return _route_active(source_path, relative)

    if top == "reference":
        return _route_reference(source_path, relative)

    if top == "archive":
        return _route_archive(source_path, relative)

    if len(parts) == 1:
        return _route_root_file(source_path, relative)

    return RoutingDecision.review(
        source_path,
        reason=f"unknown top-level directory: {top}/",
        tags=("unknown-top-level",),
    )


def _route_root_file(source_path: Path, relative: Path) -> RoutingDecision:
    """Handle loose files at the legacy memory root (e.g. 2026-04-16.md)."""
    stem = relative.stem
    if _DATE_RE.fullmatch(stem):
        return RoutingDecision.route(
            source_path,
            Path("logs/daily") / f"{stem}.md",
            reason="root-level YYYY-MM-DD file routed to daily log",
            tags=("daily",),
        )
    date_match = _DATE_RE.match(stem)
    if date_match:
        date = date_match.group(1)
        slug = slugify_path_segment(stem[len(date) + 1 :]) or "untitled"
        if "digest" in stem:
            return RoutingDecision.route(
                source_path,
                Path("digests/daily") / f"{date}.md",
                reason="root-level dated digest",
                tags=("digest",),
            )
        return RoutingDecision.review(
            source_path,
            reason=f"root-level dated file: {stem} — LLM should decide daily vs session",
            tags=("dated-root", date, slug),
        )
    return RoutingDecision.review(
        source_path,
        reason=f"root-level undated file: {stem}",
        tags=("undated-root",),
    )


def _route_ops(source_path: Path, relative: Path) -> RoutingDecision:
    """Operational reports (audits, migration plans). Keep but isolate."""
    tail = Path(*relative.parts[1:])
    return RoutingDecision.route(
        source_path,
        Path("references/reports/ops") / tail,
        reason="ops report routed to references/reports/ops",
        tags=("ops", "report"),
    )


def _route_reports(source_path: Path, relative: Path) -> RoutingDecision:
    tail = Path(*relative.parts[1:]) if len(relative.parts) > 1 else Path(relative.name)
    return RoutingDecision.route(
        source_path,
        Path("references/reports") / tail,
        reason="top-level reports/ routed to references/reports",
        tags=("report",),
    )


def _route_inbox(source_path: Path, relative: Path) -> RoutingDecision:
    tail = Path(*relative.parts[1:]) if len(relative.parts) > 1 else Path(relative.name)
    return RoutingDecision.route(
        source_path,
        Path("inbox") / tail,
        reason="inbox preserved",
        tags=("inbox",),
    )


def _route_active(source_path: Path, relative: Path) -> RoutingDecision:
    if len(relative.parts) < 2:
        return RoutingDecision.review(
            source_path,
            reason="active/ with no sub-bucket",
            tags=("active-root",),
        )

    bucket = relative.parts[1]
    tail_parts = relative.parts[2:]
    filename = relative.name
    stem = relative.stem

    if bucket == "daily":
        if stem.endswith(_DIGEST_SUFFIX):
            date_match = _DATE_RE.match(stem)
            date = date_match.group(1) if date_match else stem
            return RoutingDecision.route(
                source_path,
                Path("digests/daily") / f"{date}.md",
                reason="active daily digest → digests/daily",
                tags=("digest", "daily"),
            )
        return RoutingDecision.route(
            source_path,
            Path("logs/daily") / filename,
            reason="active daily note → logs/daily",
            tags=("daily",),
        )

    if bucket == "weekly":
        if _WEEK_RE.search(stem):
            return RoutingDecision.route(
                source_path,
                Path("logs/weekly") / filename,
                reason="active weekly note → logs/weekly",
                tags=("weekly",),
            )
        return RoutingDecision.route(
            source_path,
            Path("logs/weekly") / filename,
            reason="active weekly (non-standard name preserved) → logs/weekly",
            tags=("weekly",),
        )

    if bucket == "sessions":
        return RoutingDecision.route(
            source_path,
            Path("logs/sessions") / filename,
            reason="active session → logs/sessions",
            tags=("session",),
        )

    if bucket == "decisions":
        return RoutingDecision.route(
            source_path,
            Path("decisions") / filename,
            reason="active decision → decisions",
            tags=("decision",),
        )

    if bucket == "people":
        return RoutingDecision.route(
            source_path,
            Path("people") / filename,
            reason="active person → people",
            tags=("person",),
        )

    if bucket == "ideas":
        return RoutingDecision.route(
            source_path,
            Path("ideas") / filename,
            reason="active idea → ideas",
            tags=("idea",),
        )

    if bucket == "analysis" or bucket == "strategy":
        return RoutingDecision.route(
            source_path,
            Path("references/reports") / filename,
            reason=f"active {bucket} → references/reports",
            tags=(bucket, "report"),
        )

    if bucket == "projects":
        return _route_active_project(source_path, relative, tail_parts)

    return RoutingDecision.review(
        source_path,
        reason=f"unknown active bucket: {bucket}/",
        tags=("unknown-active-bucket", bucket),
    )


def _route_active_project(
    source_path: Path,
    relative: Path,
    tail_parts: tuple[str, ...],
) -> RoutingDecision:
    """Route a file under active/projects/. The root is
    ``active/projects/<slug>.md`` → ``projects/<slug>/state.md``.

    Subdirectory files become ``projects/<slug>/<tail>.md`` preserving
    internal structure. The ``_supporting`` pseudo-project needs LLM
    decisions to attach each file to the right real project.
    """
    filename = relative.name

    # Flat file at active/projects/<slug>.md → projects/<slug>/state.md
    if len(tail_parts) == 1:
        slug = slugify_path_segment(relative.stem) or "untitled"
        return RoutingDecision.route(
            source_path,
            Path("projects") / slug / "state.md",
            reason="active top-level project file → projects/<slug>/state.md",
            tags=("project", slug),
        )

    subdir = tail_parts[0]

    if subdir == "_supporting":
        # active/projects/_supporting/<slug>.md -> projects/<slug>/supporting.md
        # Filename stem is the project slug.
        if len(tail_parts) == 2:
            slug = slugify_path_segment(relative.stem) or "untitled"
            return RoutingDecision.route(
                source_path,
                Path("projects") / slug / "supporting.md",
                reason="active/projects/_supporting/<slug>.md → projects/<slug>/supporting.md",
                tags=("project", slug, "supporting"),
            )
        return RoutingDecision.review(
            source_path,
            reason="active/projects/_supporting/** (nested) needs LLM review",
            tags=("project", "supporting"),
        )

    slug = slugify_path_segment(subdir) or "untitled"
    # Parts after the project subdir, excluding the filename.
    inner_parts = tail_parts[1:-1]

    if filename.lower() == "readme.md" and not inner_parts:
        return RoutingDecision.route(
            source_path,
            Path("projects") / slug / "state.md",
            reason=f"active/projects/{subdir}/README.md → projects/{slug}/state.md",
            tags=("project", slug, "readme"),
        )

    if inner_parts:
        return RoutingDecision.route(
            source_path,
            Path("projects") / slug / Path(*inner_parts) / filename,
            reason=f"active/projects/{subdir}/** preserved",
            tags=("project", slug),
        )

    return RoutingDecision.route(
        source_path,
        Path("projects") / slug / filename,
        reason=f"active/projects/{subdir}/{filename} preserved",
        tags=("project", slug),
    )


def _route_reference(source_path: Path, relative: Path) -> RoutingDecision:
    if len(relative.parts) < 2:
        return RoutingDecision.review(
            source_path,
            reason="reference/ with no sub-bucket",
            tags=("reference-root",),
        )
    bucket = relative.parts[1]
    tail = Path(*relative.parts[2:]) if len(relative.parts) > 2 else Path(relative.name)

    if bucket == "knowledge":
        return RoutingDecision.route(
            source_path,
            Path("knowledge") / tail,
            reason="reference/knowledge → knowledge",
            tags=("knowledge",),
        )
    if bucket == "tools":
        return RoutingDecision.route(
            source_path,
            Path("knowledge/tools") / tail,
            reason="reference/tools → knowledge/tools",
            tags=("knowledge", "tools"),
        )
    if bucket == "health":
        return RoutingDecision.route(
            source_path,
            Path("knowledge/health") / tail,
            reason="reference/health → knowledge/health",
            tags=("knowledge", "health"),
        )
    if bucket == "resources":
        return RoutingDecision.route(
            source_path,
            Path("references/notes") / tail,
            reason="reference/resources → references/notes",
            tags=("reference", "note"),
        )
    if bucket == "tweets":
        return RoutingDecision.route(
            source_path,
            Path("references/tweets") / tail,
            reason="reference/tweets → references/tweets",
            tags=("reference", "tweets"),
        )
    if bucket == "drafts":
        return RoutingDecision.route(
            source_path,
            Path("inbox/drafts") / tail,
            reason="reference/drafts → inbox/drafts",
            tags=("inbox", "draft"),
        )
    if bucket == "supporting":
        return _route_reference_supporting(source_path, relative)

    return RoutingDecision.review(
        source_path,
        reason=f"unknown reference bucket: {bucket}/",
        tags=("unknown-reference-bucket", bucket),
    )


def _route_reference_supporting(
    source_path: Path, relative: Path
) -> RoutingDecision:
    """Handle reference/supporting/** — structured by sub-purpose.

    Sub-folders like ``project-architecture/<slug>-architecture.md`` encode
    the owning entity in the filename; we strip the suffix and route under
    ``projects/<slug>/architecture.md``. Unstructured trees land under
    ``references/supporting/**`` preserved.
    """
    if len(relative.parts) <= 3:
        return RoutingDecision.review(
            source_path,
            reason="reference/supporting/<file>.md with no sub-purpose",
            tags=("reference", "supporting"),
        )

    sub_purpose = relative.parts[2]
    filename = relative.name
    stem = relative.stem

    project_suffix_map: dict[str, tuple[str, tuple[str, ...]]] = {
        # sub_purpose -> (destination-name, filename-suffixes longest-first)
        "project-architecture": ("architecture", ("-architecture",)),
        "project-briefs": ("brief", ("-master-brief", "-brief")),
        "project-strategy": ("strategy", ("-strategy",)),
    }
    if sub_purpose in project_suffix_map:
        dest_name, trailing_suffixes = project_suffix_map[sub_purpose]
        slug = stem
        for trailing in trailing_suffixes:
            if slug.endswith(trailing):
                slug = slug[: -len(trailing)]
                break
        slug = slugify_path_segment(slug) or "untitled"
        return RoutingDecision.route(
            source_path,
            Path("projects") / slug / f"{dest_name}.md",
            reason=f"reference/supporting/{sub_purpose}/{filename} → projects/{slug}/{dest_name}.md",
            tags=("project", slug, dest_name),
        )

    if sub_purpose == "generated-output":
        return RoutingDecision.route(
            source_path,
            Path("references/reports/generated") / filename,
            reason="reference/supporting/generated-output → references/reports/generated",
            tags=("reference", "report", "generated"),
        )

    if sub_purpose == "graphs":
        return RoutingDecision.route(
            source_path,
            Path("knowledge/graphs") / filename,
            reason="reference/supporting/graphs → knowledge/graphs",
            tags=("knowledge", "graphs"),
        )

    tail = Path(*relative.parts[2:])
    return RoutingDecision.route(
        source_path,
        Path("references/supporting") / tail,
        reason=f"reference/supporting/{sub_purpose}/** preserved",
        tags=("reference", "supporting"),
    )


def _route_archive(source_path: Path, relative: Path) -> RoutingDecision:
    """Preserve archive/** as-is under the canonical corpus archive/ bucket.

    These files are tombstoned (canonical=false, status=superseded,
    source_kind=legacy) during the frontmatter normalization step that
    follows this routing. LLM dedup passes can later flag archive files
    whose live counterparts should be replaced by the archive evidence.
    """
    tail = Path(*relative.parts[1:]) if len(relative.parts) > 1 else Path(relative.name)
    if not tail.parts:
        return RoutingDecision.exclude(
            source_path, reason="archive/ root file without sub-bucket"
        )
    return RoutingDecision.route(
        source_path,
        Path("archive") / tail,
        reason="archive preserved under archive/",
        tags=("archive", "legacy"),
    )


def walk_source_tree(source_root: Path) -> list[RoutingDecision]:
    """Walk the source tree and produce one decision per file."""
    decisions: list[RoutingDecision] = []
    if not source_root.exists():
        return decisions
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        decisions.append(route_source_path(path, source_root=source_root))
    return decisions


def build_manifest(source_root: Path) -> dict[str, object]:
    """Build a JSON-serializable dry-run manifest for a source tree.

    The manifest describes what the router would do for every file
    without moving or writing anything.
    """
    decisions = walk_source_tree(source_root)
    by_kind: dict[str, int] = {"route": 0, "exclude": 0, "review": 0}
    by_destination_bucket: dict[str, int] = {}
    entries: list[dict[str, object]] = []

    for decision in decisions:
        by_kind[decision.kind] = by_kind.get(decision.kind, 0) + 1
        entry: dict[str, object] = {
            "kind": decision.kind,
            "source_path": str(decision.source_path),
            "reason": decision.reason,
            "tags": list(decision.tags),
        }
        if decision.destination is not None:
            entry["destination"] = decision.destination.as_posix()
            bucket = decision.destination.parts[0] if decision.destination.parts else ""
            if bucket:
                by_destination_bucket[bucket] = by_destination_bucket.get(bucket, 0) + 1
        entries.append(entry)

    return {
        "source_root": str(source_root),
        "total_files": len(decisions),
        "summary": {
            "by_kind": by_kind,
            "by_destination_bucket": dict(
                sorted(by_destination_bucket.items(), key=lambda kv: -kv[1])
            ),
        },
        "entries": entries,
    }
