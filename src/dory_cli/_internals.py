"""Shared helpers and runtime types for dory_cli command modules.

Moved out of `main.py` so command modules (and future subcommand splits) can
import without dragging the full CLI surface.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import typer

from dory_core.active_memory import ActiveMemoryEngine
from dory_core.config import DorySettings
from dory_core.embedding import (
    build_runtime_embedder,
)
from dory_core.llm.active_memory import build_active_memory_components
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.migration_engine import MigrationEngine, MigrationProgress
from dory_core.migration_executor import ExecutionProgress
from dory_core.migration_llm import MigrationLLM
from dory_core.migration_plan import MigrationPlan, MigrationPlanner, MigrationScope
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.research import ResearchEngine
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.wake import WakeBuilder


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path


def _get_config(ctx: typer.Context) -> RuntimeConfig:
    config = ctx.obj
    if not isinstance(config, RuntimeConfig):
        raise typer.BadParameter("CLI context is missing runtime config")
    return config


def _resolve_corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as err:
        raise typer.BadParameter(f"path escapes corpus root: {relative_path}") from err
    if not target.exists():
        raise typer.BadParameter(f"path not found: {relative_path}")
    return target


def _slice_lines(text: str, start_line: int, limit: int | None) -> str:
    lines = text.splitlines()
    if start_line < 1:
        raise typer.BadParameter("--from must be >= 1")

    start_index = start_line - 1
    end_index = len(lines) if limit is None else start_index + limit
    return "\n".join(lines[start_index:end_index])


def _resolve_proposal_path(corpus_root: Path, proposal_id: str) -> Path:
    proposals_root = corpus_root / "inbox" / "proposed"
    candidate = proposals_root / proposal_id
    if candidate.suffix != ".json":
        candidate = candidate.with_suffix(".json")
    if not candidate.exists():
        raise typer.BadParameter(f"proposal not found: {proposal_id}")
    return candidate


def _resolve_distilled_path(corpus_root: Path, distilled_id: str) -> Path:
    candidate = Path(distilled_id)
    if not candidate.is_absolute():
        if candidate.suffix == ".md":
            resolved = _resolve_corpus_path(corpus_root, str(candidate))
        else:
            resolved = corpus_root / "inbox" / "distilled" / f"{candidate.name}.md"
    else:
        resolved = candidate
    if not resolved.exists():
        raise typer.BadParameter(f"distilled note not found: {distilled_id}")
    return resolved


def _build_query_expander(settings: DorySettings) -> OpenRouterQueryExpander | None:
    if not settings.query_expansion_enabled or settings.query_expansion_max <= 0:
        return None
    client = _build_openrouter_client_for_purpose(settings, purpose="query")
    if client is None:
        return None
    return OpenRouterQueryExpander(client=client, max_expansions=settings.query_expansion_max)


def _require_openrouter_client(settings: DorySettings, *, purpose: str = "default"):
    client = _build_openrouter_client_for_purpose(settings, purpose=purpose)
    if client is None:
        _fail_with_runtime_error("OpenRouter API key is missing. Set DORY_OPENROUTER_API_KEY or OPENROUTER_API_KEY.")
    return client


def _build_openrouter_client_for_purpose(settings: DorySettings, *, purpose: str):
    try:
        return build_openrouter_client(settings, purpose=purpose)
    except TypeError:
        # Some tests monkeypatch the factory with a simpler one-argument lambda.
        return build_openrouter_client(settings)


def _build_active_memory_engine(config: RuntimeConfig) -> ActiveMemoryEngine:
    settings = DorySettings()
    planner, composer = build_active_memory_components(settings)
    query_planner = _build_retrieval_planner(settings, purpose="query")
    return ActiveMemoryEngine(
        wake_builder=WakeBuilder(config.corpus_root),
        search_engine=SearchEngine(
            config.index_root,
            build_runtime_embedder(),
            query_expander=_build_query_expander(settings),
            retrieval_planner=query_planner,
            result_selector=query_planner,
            reranker=build_reranker(settings),
            rerank_candidate_limit=settings.query_reranker_candidate_limit,
        ),
        root=config.corpus_root,
        planner=planner,
        composer=composer,
    )


def _build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    if purpose == "query" and not settings.query_planner_enabled:
        return None
    client = _build_openrouter_client_for_purpose(settings, purpose=purpose)
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)


def _build_semantic_write_engine(config: RuntimeConfig) -> SemanticWriteEngine:
    return SemanticWriteEngine(
        config.corpus_root,
        index_root=config.index_root,
        embedder=build_runtime_embedder(),
    )


def _build_migration_engine(
    config: RuntimeConfig,
    *,
    use_llm: bool = True,
    concurrency: int | None = None,
) -> MigrationEngine:
    settings = DorySettings()
    resolved_concurrency = concurrency or settings.migration_concurrency
    if not use_llm:
        return MigrationEngine(config.corpus_root, llm=None, concurrency=resolved_concurrency)
    client = build_openrouter_client(settings, purpose="maintenance")
    llm = MigrationLLM(client=client) if client is not None else None
    return MigrationEngine(config.corpus_root, llm=llm, concurrency=resolved_concurrency)


def _build_migration_planner() -> MigrationPlanner:
    return MigrationPlanner(settings=DorySettings(), live_pricing=True)


def _build_migration_scope(*, folder: list[str], sample: int | None) -> MigrationScope:
    return MigrationScope(selected_roots=tuple(folder), sample_size=sample)


def _planner_with_pricing_overrides(planner: MigrationPlanner, pricing_file: Path) -> MigrationPlanner:
    payload = json.loads(pricing_file.read_text(encoding="utf-8"))
    input_rate = float(payload["input_usd_per_million"])
    output_rate = float(payload["output_usd_per_million"])

    def _resolve(settings: DorySettings | None, purpose: str, use_live_pricing: bool = False):
        metadata = planner.metadata_resolver(settings, purpose=purpose, use_live_pricing=use_live_pricing)
        from dory_core.llm.openrouter import OpenRouterModelMetadata, OpenRouterModelPricing

        return OpenRouterModelMetadata(
            model=metadata.model,
            pricing=OpenRouterModelPricing(
                input_usd_per_million=input_rate,
                output_usd_per_million=output_rate,
            ),
        )

    return MigrationPlanner(
        settings=planner.settings,
        token_counter=planner.token_counter,
        model_purpose=planner.model_purpose,
        live_pricing=planner.live_pricing,
        classification_output_tokens=planner.classification_output_tokens,
        extraction_output_tokens=planner.extraction_output_tokens,
        preview_limit=planner.preview_limit,
        metadata_resolver=_resolve,
    )


def _build_migrate_route_progress_reporter() -> Callable[[ExecutionProgress], None] | None:
    """Print a simple [x/total] line on stderr as migrate-route processes files."""
    force_progress = DorySettings().migrate_progress
    if not force_progress and not sys.stderr.isatty():
        return None

    def _report(progress: ExecutionProgress) -> None:
        if progress.index != progress.total and progress.index % 25 != 0:
            return
        short_dest = progress.last_destination or "(skipped)"
        line = (
            f"\r[migrate-route] {progress.index}/{progress.total}  "
            f"written={progress.written} skipped={progress.skipped} errored={progress.errored}  "
            f"last: {short_dest}"
        )
        # Clear the rest of the line, then write the update without a newline.
        typer.echo(f"{line:<120}", err=True, nl=False)

    return _report


def _build_migration_progress_reporter() -> Callable[[MigrationProgress], None] | None:
    force_progress = DorySettings().migrate_progress
    if not force_progress and not sys.stderr.isatty():
        return None

    last_percent = -1
    last_phase = ""

    def _report(progress: MigrationProgress) -> None:
        nonlocal last_percent, last_phase
        if progress.percent == last_percent and progress.phase == last_phase:
            return
        last_percent = progress.percent
        last_phase = progress.phase
        scope = ""
        if progress.total_count > 0:
            scope = f" {progress.processed_count}/{progress.total_count}"
        details = progress.message or progress.path or ""
        suffix = f" {details}" if details else ""
        typer.echo(f"[migrate] {progress.percent:3d}% {progress.phase}{scope}{suffix}", err=True)

    return _report


def _run_interactive_migration_plan(
    planner: MigrationPlanner,
    legacy_root: Path,
    *,
    folder: list[str],
    sample: int | None,
) -> MigrationPlan | None:
    base_scan = planner.scan_corpus(legacy_root)
    scope = _build_migration_scope(folder=folder, sample=sample)
    while True:
        plan = planner.build_plan(base_scan, scope=scope)
        _print_interactive_migration_plan(plan)
        choice = (
            typer.prompt(
                "Choose scope [full/sample/folders/run/quit]",
                default="run" if scope.selection_mode != "full" else "sample",
            )
            .strip()
            .lower()
        )
        if choice in {"quit", "q"}:
            typer.echo("Migration cancelled.")
            return None
        if choice in {"run", "r"}:
            if not typer.confirm("Run migration with this scope?", default=True):
                continue
            return plan
        if choice in {"full", "f"}:
            scope = MigrationScope()
            continue
        if choice in {"sample", "s"}:
            selected = typer.prompt("Sample size", default=str(scope.sample_size or 25)).strip()
            scope = MigrationScope(selected_roots=scope.selected_roots, sample_size=max(1, int(selected)))
            continue
        if choice in {"folders", "folder"}:
            scope = MigrationScope(selected_roots=_prompt_folder_selection(base_scan))
            continue
        typer.echo("Unknown choice. Use full, sample, folders, run, or quit.", err=True)


def _prompt_folder_selection(scan) -> tuple[str, ...]:
    typer.echo("Available top-level folders:")
    for index, stat in enumerate(scan.folder_stats, start=1):
        typer.echo(f"  {index}. {stat.folder} ({stat.markdown_count} files)")
    raw = typer.prompt("Select folders by number or name (comma-separated)", default="memory").strip()
    selected: list[str] = []
    for part in [item.strip() for item in raw.split(",") if item.strip()]:
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(scan.folder_stats):
                selected.append(scan.folder_stats[idx].folder)
            continue
        selected.append(part.lower())
    return tuple(dict.fromkeys(selected))


def _print_interactive_migration_plan(plan: MigrationPlan) -> None:
    estimate = plan.estimate
    typer.echo("")
    typer.echo("Migration preflight")
    typer.echo(f"  Legacy root: {plan.scan.corpus_root}")
    typer.echo(f"  Scope: {plan.scope.selection_mode}")
    if plan.scope.selected_roots:
        typer.echo(f"  Folders: {', '.join(plan.scope.selected_roots)}")
    if plan.scope.sample_size is not None:
        typer.echo(f"  Sample size: {plan.scope.sample_size}")
    typer.echo(f"  Files: {plan.selected_markdown_count} / {plan.scan.markdown_count}")
    typer.echo(f"  Bytes: {plan.selected_byte_count}")
    typer.echo(f"  Model: {estimate.model_name or estimate.model}")
    typer.echo(f"  Pricing source: {estimate.pricing_source}")
    typer.echo(f"  Input tokens: {estimate.estimated_input_tokens}")
    typer.echo(f"  Output tokens (estimated): {estimate.estimated_output_tokens}")
    if estimate.estimated_total_usd is not None:
        typer.echo(f"  Estimated spend: ${estimate.estimated_total_usd:.4f}")
    typer.echo("  Preview:")
    for path in plan.preview_files:
        typer.echo(f"    - {path.relative_to(plan.scan.corpus_root).as_posix()}")
    remaining = plan.selected_markdown_count - len(plan.preview_files)
    if remaining > 0:
        typer.echo(f"    ... {remaining} more")
    typer.echo("")


def _serialize_migration_plan(plan: MigrationPlan) -> dict[str, object]:
    estimate = plan.estimate
    return {
        "legacy_root": str(plan.scan.corpus_root),
        "scope": {
            "mode": plan.scope.selection_mode,
            "folders": list(plan.scope.selected_roots),
            "sample_size": plan.scope.sample_size,
        },
        "scan": {
            "markdown_count": plan.scan.markdown_count,
            "byte_count": plan.scan.byte_count,
            "folders": [
                {
                    "folder": stat.folder,
                    "markdown_count": stat.markdown_count,
                    "byte_count": stat.byte_count,
                }
                for stat in plan.scan.folder_stats
            ],
        },
        "selected": {
            "markdown_count": plan.selected_markdown_count,
            "byte_count": plan.selected_byte_count,
            "preview_files": [path.relative_to(plan.scan.corpus_root).as_posix() for path in plan.preview_files],
        },
        "estimate": {
            "model": estimate.model,
            "model_name": estimate.model_name,
            "pricing_available": estimate.pricing is not None,
            "pricing_source": estimate.pricing_source,
            "classification_input_tokens": estimate.classification_input_tokens,
            "classification_output_tokens": estimate.classification_output_tokens,
            "extraction_input_tokens": estimate.extraction_input_tokens,
            "extraction_output_tokens": estimate.extraction_output_tokens,
            "estimated_input_tokens": estimate.estimated_input_tokens,
            "estimated_output_tokens": estimate.estimated_output_tokens,
            "estimated_total_tokens": estimate.estimated_total_tokens,
            "estimated_input_usd": estimate.estimated_input_usd,
            "estimated_output_usd": estimate.estimated_output_usd,
            "estimated_total_usd": estimate.estimated_total_usd,
        },
    }


def _build_research_engine(config: RuntimeConfig) -> ResearchEngine:
    settings = DorySettings()
    planner = _build_retrieval_planner(settings, purpose="query")
    return ResearchEngine(
        search_engine=SearchEngine(
            config.index_root,
            build_runtime_embedder(),
            query_expander=_build_query_expander(settings),
            retrieval_planner=planner,
            result_selector=planner,
            reranker=build_reranker(settings),
            rerank_candidate_limit=settings.query_reranker_candidate_limit,
        )
    )


def _infer_agent_from_session_path(session_path: str) -> str:
    parts = Path(session_path).parts
    if len(parts) >= 3 and parts[0] == "logs" and parts[1] == "sessions":
        return parts[2]
    return "codex"


def _fail_with_runtime_error(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=2)


def _init_directories(config: RuntimeConfig) -> list[Path]:
    return [
        config.corpus_root,
        config.corpus_root / "core",
        config.corpus_root / "inbox",
        config.corpus_root / "inbox" / "proposed",
        config.corpus_root / "inbox" / "applied",
        config.corpus_root / "inbox" / "maintenance",
        config.corpus_root / "inbox" / "rejected",
        config.corpus_root / "inbox" / "distilled",
        config.corpus_root / "logs" / "sessions",
        config.index_root,
        config.auth_tokens_path.parent,
    ]


def _init_seed_documents(corpus_root: Path) -> dict[Path, str]:
    created = date.today().isoformat()
    return {
        corpus_root / "core" / "user.md": _render_seed_doc(
            title="User",
            created=created,
            body="Describe the user here.",
        ),
        corpus_root / "core" / "soul.md": _render_seed_doc(
            title="Soul",
            created=created,
            body="Describe the operating principles here.",
        ),
        corpus_root / "core" / "env.md": _render_seed_doc(
            title="Environment",
            created=created,
            body="Describe the environment and constraints here.",
        ),
        corpus_root / "core" / "active.md": _render_seed_doc(
            title="Active Work",
            created=created,
            body="Describe the current active work here.",
        ),
    }


def _render_seed_doc(*, title: str, created: str, body: str) -> str:
    return f"---\ntitle: {title}\ncreated: {created}\ntype: core\nstatus: active\n---\n\n{body}\n"
