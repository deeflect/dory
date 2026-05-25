"""
Compiler plane for Dory memory kernel.

Defines the typed contract for compiler jobs: jobs that transform evidence into
reviewable artifacts and optionally promote them to canonical memory.

Every compiler job fits the pipeline:

    input evidence -> derived candidate -> reviewable proposal/artifact -> optional canonical promotion

Rules:
- Compiler jobs can be async or scheduled.
- Compiler jobs produce reviewable artifacts.
- No silent canonical rewrite from raw sessions.
- No hot-path LLM dependency.
- Context fencing prevents recalled memory blocks from being re-ingested as new facts.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Pipeline Registry ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompilerPipeline:
    """Describes one compiler pipeline from input to output.

    This is a metadata descriptor, not an executor. Actual execution is
    handled by the existing runner classes (DreamOnceRunner, etc.).
    """

    name: str
    """Short unique identifier for this pipeline."""

    input_kinds: tuple[str, ...]
    """Kinds of evidence consumed as input."""

    output_kind: str
    """Kind of artifact produced."""

    output_is_reviewable: bool
    """Whether the output should be reviewed before promotion."""

    can_promote: bool
    """Whether this pipeline supports promotion to canonical memory."""

    uses_llm: bool
    """Whether this pipeline depends on an LLM for generation."""

    is_schedulable: bool
    """Whether this pipeline can be run on a schedule (vs. on-demand only)."""

    description: str
    """Human-readable summary of the pipeline."""

    runner_class: str | None = None
    """Fully-qualified class name of the runner that executes this pipeline."""

    cli_command: str | None = None
    """CLI command string if available (e.g., 'ops dream-once')."""


# ── Full Inventory ─────────────────────────────────────────────────────

_COMPILER_PIPELINES: dict[str, CompilerPipeline] = {
    "session_distillation": CompilerPipeline(
        name="session_distillation",
        input_kinds=("raw_session",),
        output_kind="distilled",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=True,
        is_schedulable=True,
        description=(
            "Raw session files -> distilled markdown notes under inbox/distilled/. "
            "Session text is summarized with key facts, decisions, follow-ups, and entities. "
            "The distilled note references the original session path."
        ),
        runner_class="dory_core.dreaming.extract.LLMSessionDistiller",
        cli_command="ops dream-once --session",
    ),
    "proposal_generation": CompilerPipeline(
        name="proposal_generation",
        input_kinds=("distilled", "digest", "recall_promotion"),
        output_kind="proposal",
        output_is_reviewable=True,
        can_promote=True,
        uses_llm=True,
        is_schedulable=True,
        description=(
            "Distilled notes, digests, or recall-promotion notes -> reviewable proposal "
            "JSON files under inbox/proposed/. Each proposal contains typed actions "
            "(write/replace/forget) that can be applied or rejected."
        ),
        runner_class="dory_core.dreaming.proposals.ProposalGenerator",
        cli_command="ops dream-once",
    ),
    "daily_digest": CompilerPipeline(
        name="daily_digest",
        input_kinds=("session",),
        output_kind="digest",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=True,
        is_schedulable=True,
        description=(
            "Session logs for a single day -> daily digest markdown under digests/daily/. "
            "Summarizes the day's activity, decisions, and follow-ups."
        ),
        runner_class="dory_core.digest_writer.DailyDigestWriter",
        cli_command="ops daily-digest-once",
    ),
    "weekly_digest": CompilerPipeline(
        name="weekly_digest",
        input_kinds=("daily_digest",),
        output_kind="digest",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=True,
        is_schedulable=True,
        description=(
            "Daily digests for a week -> weekly digest markdown under digests/weekly/. "
            "Aggregates outcomes, decisions, and follow-ups at a weekly granularity."
        ),
        runner_class="dory_core.digest_writer.WeeklyDigestWriter",
        cli_command="ops weekly-digest-once",
    ),
    "wiki_refresh": CompilerPipeline(
        name="wiki_refresh",
        input_kinds=("canonical_page", "claim"),
        output_kind="wiki_page",
        output_is_reviewable=False,
        can_promote=False,
        uses_llm=False,
        is_schedulable=True,
        description=(
            "Canonical pages + claim store -> compiled wiki pages under wiki/. "
            "Deterministic rendering of compiled cards from claims and frontmatter. "
            "No LLM dependency. Prunes stale generated pages."
        ),
        runner_class="dory_core.ops.run_compiled_wiki_refresh",
        cli_command="ops wiki-refresh-once",
    ),
    "wiki_index_refresh": CompilerPipeline(
        name="wiki_index_refresh",
        input_kinds=("wiki_page",),
        output_kind="wiki_index",
        output_is_reviewable=False,
        can_promote=False,
        uses_llm=False,
        is_schedulable=True,
        description=(
            "Wiki pages -> regenerated index pages (people/projects/concepts/decisions indexes). "
            "Deterministic. No LLM dependency."
        ),
        runner_class="dory_core.ops.run_wiki_index_refresh",
        cli_command="ops wiki-refresh-indexes",
    ),
    "wiki_health": CompilerPipeline(
        name="wiki_health",
        input_kinds=("canonical_page", "wiki_page"),
        output_kind="maintenance_report",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=False,
        is_schedulable=True,
        description=(
            "Canonical and wiki pages -> health inspection report. "
            "Reports stale content, missing frontmatter, orphaned files, and structural issues. "
            "No LLM dependency."
        ),
        runner_class="dory_core.ops.WikiHealthRunner",
        cli_command="ops wiki-health",
    ),
    "maintenance_report": CompilerPipeline(
        name="maintenance_report",
        input_kinds=("canonical_page",),
        output_kind="maintenance_report",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=True,
        is_schedulable=True,
        description=(
            "Canonical pages -> LLM-suggested maintenance actions under inbox/maintenance/. "
            "Each report suggests type, status, area, canonical flag, and target path changes."
        ),
        runner_class="dory_core.ops.MaintenanceOnceRunner",
        cli_command="ops maintain-once",
    ),
    "recall_promotion": CompilerPipeline(
        name="recall_promotion",
        input_kinds=("recall_event",),
        output_kind="distilled",
        output_is_reviewable=True,
        can_promote=True,
        uses_llm=False,
        is_schedulable=True,
        description=(
            "Frequently-recalled memory paths -> distilled notes under inbox/distilled/recall-*. "
            "Uses OpenClaw recall event store. Deterministic candidate collection and writing. "
            "Output notes are consumed by proposal_generation for optional canonical promotion."
        ),
        runner_class="dory_core.dreaming.recall.RecallPromotionRunner",
        cli_command="ops dream-once (automatic)",
    ),
    "session_ingest": CompilerPipeline(
        name="session_ingest",
        input_kinds=("raw_session",),
        output_kind="session",
        output_is_reviewable=False,
        can_promote=False,
        uses_llm=False,
        is_schedulable=True,
        description=(
            "Raw session markdown files -> session plane index. "
            "No LLM dependency. Session content is indexed for FTS5 search "
            "but is never silently promoted to canonical memory."
        ),
        runner_class="dory_core.session_ingest",
        cli_command="session-ingest",
    ),
    "proposal_apply": CompilerPipeline(
        name="proposal_apply",
        input_kinds=("proposal",),
        output_kind="canonical",
        output_is_reviewable=False,
        can_promote=True,
        uses_llm=False,
        is_schedulable=False,
        description=(
            "Approved proposal JSON -> canonical memory write. "
            "On-demand only. Validates that the proposal's dry-run still matches "
            "before applying. Moves the proposal from inbox/proposed/ to inbox/applied/."
        ),
        runner_class="dory_core.dreaming.proposals.apply_proposal",
        cli_command="proposal-apply",
    ),
}


# ── Lookup Helpers ─────────────────────────────────────────────────────


def list_compiler_pipelines() -> dict[str, CompilerPipeline]:
    """Return the full inventory of compiler pipelines."""
    return dict(_COMPILER_PIPELINES)


def get_pipeline(name: str) -> CompilerPipeline | None:
    """Look up a compiler pipeline by name."""
    return _COMPILER_PIPELINES.get(name)


def pipelines_by_output_kind(kind: str) -> tuple[CompilerPipeline, ...]:
    """Return all pipelines that produce the given output kind."""
    return tuple(p for p in _COMPILER_PIPELINES.values() if p.output_kind == kind)


def pipelines_by_input_kind(kind: str) -> tuple[CompilerPipeline, ...]:
    """Return all pipelines that consume the given input kind."""
    return tuple(p for p in _COMPILER_PIPELINES.values() if kind in p.input_kinds)


def schedulable_pipelines() -> tuple[CompilerPipeline, ...]:
    """Return all pipelines that can be run on a schedule."""
    return tuple(p for p in _COMPILER_PIPELINES.values() if p.is_schedulable)


def llm_free_pipelines() -> tuple[CompilerPipeline, ...]:
    """Return all pipelines that do not require an LLM."""
    return tuple(p for p in _COMPILER_PIPELINES.values() if not p.uses_llm)


# ── Context Fencing ────────────────────────────────────────────────────

COMPILER_INBOX_PREFIXES: tuple[str, ...] = (
    "inbox/distilled/",
    "inbox/proposed/",
    "inbox/applied/",
    "inbox/rejected/",
    "inbox/maintenance/",
    "inbox/semantic/",
)

RECALL_SOURCE_PREFIXES: tuple[str, ...] = (
    "inbox/distilled/recall-",
)

COMPILER_OUTPUT_PREFIXES: tuple[str, ...] = (
    "wiki/",
    "inbox/",
    "digests/",
    "eval/runs/",
)


def is_compiler_artifact(rel_path: str) -> bool:
    """Return True if *rel_path* lives in a compiler output directory.

    Compiler artifacts are derived outputs — they must not be re-ingested
    as primary evidence.
    """
    return rel_path.startswith(COMPILER_OUTPUT_PREFIXES)


def is_recall_promotion_artifact(rel_path: str) -> bool:
    """Return True if *rel_path* is a recall-promotion distilled note.

    Recall promotion artifacts are derived from recall events and reference
    existing durable memory. They must not be re-ingested as new facts.
    """
    return rel_path.startswith(RECALL_SOURCE_PREFIXES)


def is_inbox_path(rel_path: str) -> bool:
    """Return True if *rel_path* is under one of the compiler inbox directories."""
    return rel_path.startswith(COMPILER_INBOX_PREFIXES)


def context_fence_for_ingest(rel_path: str) -> str | None:
    """Check whether *rel_path* can be safely re-ingested as evidence.

    Returns a warning string if the path should be fenced (blocked from
    re-ingestion), or *None* if it is safe to treat as primary evidence.
    """
    if is_recall_promotion_artifact(rel_path):
        return (
            f"recall promotion artifact {rel_path} cannot be re-ingested as new evidence; "
            "recalled memory blocks are derived from existing durable memory and would "
            "create a feedback loop"
        )
    if is_inbox_path(rel_path):
        return (
            f"compiler artifact {rel_path} cannot be re-ingested as raw evidence; "
            "compiler outputs are derived from primary sources, not primary sources themselves"
        )
    if rel_path.startswith("wiki/"):
        return (
            f"generated wiki page {rel_path} cannot be re-ingested as raw evidence; "
            "compiled wiki pages are derived from canonical pages and claims"
        )
    if rel_path.startswith("digests/"):
        return (
            f"digest page {rel_path} cannot be re-ingested as raw evidence; "
            "digests are compiled summaries, not primary source material"
        )
    return None


def assert_safe_to_ingest(rel_path: str) -> None:
    """Raise *ValueError* if *rel_path* is a compiler artifact that must not be re-ingested."""
    warning = context_fence_for_ingest(rel_path)
    if warning is not None:
        raise ValueError(warning)


# ── Compiler Artifact ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompilerArtifact:
    """A reviewable artifact produced by a compiler job.

    Tracks the artifact's lifecycle: source evidence, derived content,
    review status, and optional canonical promotion.
    """

    kind: str
    """Artifact kind matching one of the output_kind values from CompilerPipeline."""

    source_paths: tuple[str, ...]
    """Paths of the input evidence that produced this artifact."""

    artifact_path: str
    """Path of the produced artifact, relative to corpus root."""

    producer: str
    """Name of the CompilerPipeline that produced this artifact."""

    status: str = "pending"
    """Review status: pending, applied, rejected, or archived."""

    pipeline_name: str | None = None
    """Convenience alias for producer; populated automatically if None."""
