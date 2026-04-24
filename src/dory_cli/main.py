from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path

import typer

from dory_cli._internals import (
    RuntimeConfig,
    _build_active_memory_engine,
    _build_migrate_route_progress_reporter,
    _build_migration_engine,
    _build_migration_planner,
    _build_migration_progress_reporter,
    _build_migration_scope,
    _build_query_expander,
    _build_research_engine,
    _build_retrieval_planner,
    _build_semantic_write_engine,
    _fail_with_runtime_error,
    _get_config,
    _infer_agent_from_session_path,
    _init_directories,
    _init_seed_documents,
    _planner_with_pricing_overrides,
    _require_openrouter_client,
    _resolve_corpus_path,
    _resolve_distilled_path,
    _resolve_proposal_path,
    _run_interactive_migration_plan,
    _serialize_migration_plan,
    _slice_lines,
)
from dory_cli.eval import app as eval_app
from dory_core.artifacts import ArtifactWriter
from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.dreaming.events import SessionClosedEvent
from dory_core.dreaming.extract import DistillationWriter, OpenRouterSessionDistiller
from dory_core.dreaming.proposals import ProposalGenerator, list_proposals, load_proposal
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError, build_runtime_embedder
from dory_core.index.reindex import (
    ReconcilePlan,
    ReindexProgress,
    plan_reconcile,
    reconcile_corpus,
    reindex_corpus,
    reindex_paths,
)
from dory_core.link import LinkService
from dory_core.llm.dream import build_dream_llm, require_dream_llm
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.maintenance import MaintenanceReportWriter, OpenRouterMaintenanceInspector, PrivacyMetadataBackfiller
from dory_core.claim_store import ClaimStore
from dory_core.digest_mining import (
    OpenRouterDigestExtractor,
    format_mining_summary,
    mine_digest_file,
    mine_digest_tree,
)
from dory_core.digest_writer import (
    DailyDigestWriter,
    OpenRouterDailyDigestGenerator,
    OpenRouterWeeklyDigestGenerator,
    WeeklyDigestWriter,
    current_iso_week,
    previous_day,
    previous_iso_week,
)
from dory_core.migration_batching import build_batches, format_batching_summary
from dory_core.migration_core_seed import format_seed_summary, seed_core_from_root
from dory_core.migration_entity_discovery import (
    discover_entities,
    format_discovery_summary,
    write_entities,
)
from dory_core.migration_entity_synthesis import (
    format_synthesis_summary,
    load_entities_from_json,
    synthesize_entities,
)
from dory_core.migration_idea_promotion import format_promotion_summary, promote_ideas
from dory_core.migration_executor import (
    execute_manifest,
    execute_source_tree,
)
from dory_core.migration_review_router import OpenRouterReviewRouter
from dory_core.migration_source_router import build_manifest, walk_source_tree
from dory_core.ops import (
    DreamOnceRunner,
    EvalOnceRunner,
    MaintenanceOnceRunner,
    OpsWatchRunner,
    WikiHealthRunner,
    serialize_result,
)
from dory_core.ops import run_compiled_wiki_refresh, run_wiki_index_refresh
from dory_core.purge import PurgeEngine
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.session_sync import plan_session_sync, sync_session_files
from dory_core.status import build_status, format_status
from dory_core.types import (
    ActiveMemoryReq,
    MemoryWriteReq,
    PurgeReq,
    ResearchReq,
    SearchReq,
    SearchScope,
    WakeReq,
    serialize_search_response,
)
from dory_core.wake import WakeBuilder
from dory_http.auth import issue_token

app = typer.Typer(add_completion=False, help="Dory CLI")
app.add_typer(eval_app, name="eval")
auth_app = typer.Typer(add_completion=False, help="Manage bearer tokens.")
app.add_typer(auth_app, name="auth")
dream_app = typer.Typer(add_completion=False, help="Review and apply dreaming proposals.")
app.add_typer(dream_app, name="dream")
maintain_app = typer.Typer(add_completion=False, help="Inspect corpus docs and emit maintenance suggestions.")
app.add_typer(maintain_app, name="maintain")
ops_app = typer.Typer(add_completion=False, help="Operator-first batch jobs and watch loops.")
app.add_typer(ops_app, name="ops")


def run() -> None:
    app()



@app.callback()
def main(
    ctx: typer.Context,
    corpus_root: Path | None = typer.Option(None, "--corpus-root", help="Path to the Dory corpus"),
    index_root: Path | None = typer.Option(None, "--index-root", help="Path to the Dory index"),
    auth_tokens_path: Path | None = typer.Option(
        None,
        "--auth-tokens-path",
        help="Path to the HTTP bearer token store",
    ),
) -> None:
    runtime_paths = resolve_runtime_paths(
        corpus_root=corpus_root,
        index_root=index_root,
        auth_tokens_path=auth_tokens_path,
    )
    ctx.obj = RuntimeConfig(
        corpus_root=runtime_paths.corpus_root,
        index_root=runtime_paths.index_root,
        auth_tokens_path=runtime_paths.auth_tokens_path,
    )


@app.command()
def init(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    created: list[str] = []

    for directory in _init_directories(config):
        directory.mkdir(parents=True, exist_ok=True)
        created.append(str(directory))

    for target, body in _init_seed_documents(config.corpus_root).items():
        if target.exists():
            continue
        target.write_text(body, encoding="utf-8")

    if not config.auth_tokens_path.exists():
        config.auth_tokens_path.write_text("{}\n", encoding="utf-8")

    typer.echo(
        json.dumps(
            {
                "corpus_root": str(config.corpus_root),
                "index_root": str(config.index_root),
                "auth_tokens_path": str(config.auth_tokens_path),
                "initialized": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command()
def wake(
    ctx: typer.Context,
    budget: int = typer.Option(600, "--budget"),
    agent: str = typer.Option("codex", "--agent"),
    profile: str = typer.Option("default", "--profile"),
    project: str | None = typer.Option(None, "--project", help="Optional project/entity handle to include in wake."),
) -> None:
    config = _get_config(ctx)
    resp = WakeBuilder(config.corpus_root).build(
        WakeReq(budget_tokens=budget, agent=agent, profile=profile, project=project)
    )
    typer.echo(resp.block)


@app.command("active-memory")
def active_memory(
    ctx: typer.Context,
    prompt: str = typer.Argument(...),
    agent: str = typer.Option("codex", "--agent"),
    cwd: str | None = typer.Option(None, "--cwd"),
    profile: str = typer.Option("auto", "--profile"),
    include_wake: bool = typer.Option(True, "--include-wake/--no-include-wake"),
) -> None:
    config = _get_config(ctx)
    result = _build_active_memory_engine(config).build(
        ActiveMemoryReq(
            prompt=prompt,
            agent=agent,
            cwd=cwd,
            profile=profile,
            include_wake=include_wake,
        )
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("memory-write")
def memory_write(
    ctx: typer.Context,
    content: str = typer.Argument(..., help="Memory content to write"),
    subject: str = typer.Option(..., "--subject", help="Fuzzy subject to route the memory to"),
    action: str = typer.Option("write", "--action", help="Semantic write action"),
    kind: str = typer.Option("fact", "--kind", help="Semantic memory kind"),
    scope: str | None = typer.Option(None, "--scope", help="Optional routing scope"),
    confidence: str | None = typer.Option(None, "--confidence", help="Optional confidence hint"),
    reason: str | None = typer.Option(None, "--reason", help="Optional reason or context"),
    source: str | None = typer.Option(None, "--source", help="Optional source label"),
    soft: bool = typer.Option(False, "--soft/--no-soft", help="Quarantine instead of rejecting on ambiguity"),
    dry_run: bool = typer.Option(False, "--dry-run/--no-dry-run", help="Preview routing without writing"),
    force_inbox: bool = typer.Option(
        False, "--force-inbox/--no-force-inbox", help="Bypass subject resolution and capture under inbox/semantic"
    ),
    allow_canonical: bool = typer.Option(
        False,
        "--allow-canonical/--no-allow-canonical",
        help="Permit a live semantic write to canonical memory after preview",
    ),
) -> None:
    config = _get_config(ctx)
    request = MemoryWriteReq.model_validate(
        {
            "action": action,
            "kind": kind,
            "subject": subject,
            "content": content,
            "scope": scope,
            "confidence": confidence,
            "reason": reason,
            "source": source,
            "soft": soft,
            "dry_run": dry_run,
            "force_inbox": force_inbox,
            "allow_canonical": allow_canonical,
        }
    )
    result = _build_semantic_write_engine(config).write(request)
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("purge")
def purge(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Exact corpus-relative markdown path to hard-delete"),
    expected_hash: str | None = typer.Option(None, "--expected-hash", help="Required for live purge"),
    reason: str | None = typer.Option(None, "--reason", help="Required for live purge"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run", help="Preview by default; pass --no-dry-run to delete"
    ),
    allow_canonical: bool = typer.Option(
        False, "--allow-canonical/--no-allow-canonical", help="Permit protected/canonical paths"
    ),
    include_related_tombstone: bool = typer.Option(
        False,
        "--include-related-tombstone/--no-include-related-tombstone",
        help="Also delete <target>.tombstone.md when present",
    ),
) -> None:
    config = _get_config(ctx)
    request = PurgeReq(
        target=target,
        expected_hash=expected_hash,
        reason=reason,
        dry_run=dry_run,
        allow_canonical=allow_canonical,
        include_related_tombstone=include_related_tombstone,
    )
    embedder = None if dry_run else build_runtime_embedder()
    result = PurgeEngine(
        root=config.corpus_root,
        index_root=config.index_root,
        embedder=embedder,
    ).purge(request)
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command()
def research(
    ctx: typer.Context,
    question: str = typer.Argument(...),
    kind: str = typer.Option("report", "--kind"),
    corpus: str = typer.Option("all", "--corpus"),
    limit: int = typer.Option(8, "--limit"),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    config = _get_config(ctx)
    engine = _build_research_engine(config)
    research_resp = engine.research_from_req(
        ResearchReq(
            question=question,
            kind=kind,  # type: ignore[arg-type]
            corpus=corpus,  # type: ignore[arg-type]
            limit=limit,
            save=save,
        )
    )
    if save:
        artifact_resp = ArtifactWriter(
            config.corpus_root,
            index_root=config.index_root,
            embedder=build_runtime_embedder(),
        ).write(
            research_resp.artifact,
            created=str(date.today()),
        )
    else:
        artifact_resp = None
    typer.echo(
        json.dumps(
            {
                "artifact": artifact_resp.model_dump(mode="json") if artifact_resp is not None else None,
                "research": research_resp.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command()
def migrate(
    ctx: typer.Context,
    legacy_root: Path = typer.Argument(..., help="Path to the legacy corpus root"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Use OpenRouter semantic migration when configured"),
    jobs: int | None = typer.Option(None, "--jobs", min=1, help="Parallel classify/extract workers"),
    estimate: bool = typer.Option(False, "--estimate", help="Show a preflight estimate without running migration"),
    interactive: bool = typer.Option(
        False, "--interactive", help="Run an interactive migration selector in the terminal"
    ),
    folder: list[str] = typer.Option([], "--folder", help="Restrict migration to top-level legacy folders"),
    sample: int | None = typer.Option(None, "--sample", min=1, help="Run an evenly sampled subset of markdown files"),
    pricing_file: Path | None = typer.Option(
        None,
        "--pricing-file",
        help="Optional JSON file with input/output price-per-million overrides",
    ),
) -> None:
    """Stage a legacy corpus into Dory.

    Use --jobs to run parallel classify/extract workers.
    """
    config = _get_config(ctx)
    planner = _build_migration_planner()
    if pricing_file is not None:
        planner = _planner_with_pricing_overrides(planner, pricing_file)
    if interactive:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            _fail_with_runtime_error("`dory migrate --interactive` requires an interactive TTY.")
        plan = _run_interactive_migration_plan(planner, legacy_root, folder=folder, sample=sample)
        if plan is None:
            raise typer.Exit(code=0)
    else:
        plan = planner.plan_corpus(legacy_root, scope=_build_migration_scope(folder=folder, sample=sample))
    if estimate:
        typer.echo(json.dumps(_serialize_migration_plan(plan), indent=2, sort_keys=True))
        return
    progress_callback = _build_migration_progress_reporter()
    result = _build_migration_engine(config, use_llm=use_llm, concurrency=jobs).migrate(
        legacy_root,
        progress=progress_callback,
        selected_paths=plan.selected_markdown_files,
    )
    typer.echo(json.dumps(asdict(result), indent=2, sort_keys=True))


@app.command("migrate-route")
def migrate_route(
    source_root: Path = typer.Argument(..., help="Path to the legacy memory root"),
    corpus_root: Path = typer.Argument(..., help="Target Dory corpus root"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the migration without writing files"),
    include_review: bool = typer.Option(
        False,
        "--include-review",
        help="Also execute review-case decisions (risky — usually want LLM routing first)",
    ),
    llm_route: bool = typer.Option(
        False,
        "--llm-route",
        help="Upgrade review-case decisions via LLM before executing (requires OpenRouter)",
    ),
    core_from: Path | None = typer.Option(
        None,
        "--core-from",
        help="Additional path whose UPPERCASE-stem *.md files seed core/",
    ),
    do_reindex: bool = typer.Option(False, "--reindex", help="Reindex the corpus after routing"),
    do_mine: bool = typer.Option(
        False,
        "--mine-digests",
        help="Mine claims out of digests after routing (requires OpenRouter)",
    ),
    do_entities: bool = typer.Option(
        False,
        "--entities",
        help="Discover entities + synthesize canonical pages after routing (requires OpenRouter)",
    ),
    do_promote_ideas: bool = typer.Option(
        False,
        "--promote-ideas",
        help="Classify and promote idea files to concept/project pages (requires OpenRouter)",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help=(
            "Implies --llm-route, --reindex, --entities, --promote-ideas, "
            "--mine-digests, and --core-from <source_root.parent>. The full pipeline."
        ),
    ),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Process only the first N files"),
) -> None:
    """Execute the deterministic router's decisions against a target corpus.

    Walks ``source_root``, runs the source router on every markdown file,
    and writes each routed file into the corresponding destination under
    ``corpus_root``. Files routed to ``archive/`` are automatically
    tombstoned (canonical=false, status=superseded, source_kind=legacy).
    Bare files (no frontmatter) get minimal synthesized frontmatter.

    With ``--llm-route``, review-case files get upgraded to routed
    decisions via LLM. With ``--core-from PATH``, uppercase-stem
    markdown files at PATH seed core/. With ``--reindex``, the corpus
    is indexed after routing. With ``--mine-digests``, digests are LLM-
    mined into structured claims.

    ``--full`` turns on everything in order: core seed, routing,
    reindex, mine-digests. This is what most users want.
    """
    if not source_root.exists():
        _fail_with_runtime_error(f"source root does not exist: {source_root}")
    if not corpus_root.exists() and not dry_run:
        corpus_root.mkdir(parents=True, exist_ok=True)

    if full:
        llm_route = True
        do_reindex = True
        do_mine = True
        do_entities = True
        do_promote_ideas = True
        if core_from is None:
            core_from = source_root.parent

    settings = DorySettings()
    paths = resolve_runtime_paths(
        corpus_root=corpus_root,
        index_root=corpus_root / ".index",
    )

    if core_from is not None:
        typer.echo(f"→ seeding core/ from {core_from}", err=True)
        seed_result = seed_core_from_root(core_from, corpus_root, dry_run=dry_run)
        seed_summary = format_seed_summary(seed_result)
        typer.echo(
            f"  seeded {seed_summary['copied_count']} file(s): "
            f"{', '.join(Path(p).name for p in seed_result.copied) or '(none)'}",
            err=True,
        )

    progress_reporter = _build_migrate_route_progress_reporter()

    if llm_route:
        settings = DorySettings()
        client = build_openrouter_client(settings, purpose="maintenance")
        if client is None:
            _fail_with_runtime_error("--llm-route requires an OpenRouter API key")
        review_router = OpenRouterReviewRouter(client=client)
        typer.echo("→ walking source tree and resolving review cases via LLM…", err=True)
        decisions = walk_source_tree(source_root)
        review_count = sum(1 for d in decisions if d.kind == "review")
        if review_count:
            typer.echo(f"  {review_count} review case(s) to LLM-route", err=True)
        decisions = [review_router.resolve(d) if d.kind == "review" else d for d in decisions]
        typer.echo(f"→ executing {len(decisions)} decisions…", err=True)
        report = execute_manifest(
            decisions,
            source_root=source_root,
            corpus_root=corpus_root,
            dry_run=dry_run,
            include_review=include_review,
            limit=limit,
            progress=progress_reporter,
        )
    else:
        typer.echo("→ walking source tree…", err=True)
        report = execute_source_tree(
            source_root,
            corpus_root,
            dry_run=dry_run,
            include_review=include_review,
            limit=limit,
            progress=progress_reporter,
        )
    typer.echo("", err=True)  # newline after the last progress line

    summary: dict[str, object] = {
        "total_decisions": report.total_decisions,
        "routed": report.routed,
        "excluded": report.excluded,
        "reviewed": report.reviewed,
        "written": report.written,
        "skipped": report.skipped,
        "errored": report.errored,
        "dry_run": dry_run,
    }

    if dry_run:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return

    def _require_openrouter(pass_name: str) -> OpenRouterClient | None:
        client = build_openrouter_client(settings, purpose="dream")
        if client is None:
            summary[f"{pass_name}_error"] = "OpenRouter API key missing"
            typer.echo(f"  {pass_name} skipped: no OpenRouter key", err=True)
        return client

    entities_path = paths.corpus_root / ".dory" / "entities.json"

    if do_entities:
        discovery_client = _require_openrouter("entity_discovery")
        if discovery_client is not None:
            typer.echo("→ discovering entities (batched LLM scan)…", err=True)
            batches = build_batches(paths.corpus_root)
            batch_summary = format_batching_summary(batches)
            typer.echo(
                f"  {batch_summary['total_batches']} batches, "
                f"{batch_summary['total_files']} files, "
                f"{batch_summary['total_tokens']:,} tokens",
                err=True,
            )
            report = discover_entities(
                paths.corpus_root,
                batches,
                client=discovery_client,
                progress=lambda *, phase, index, total, label: typer.echo(
                    f"\r  [entity-discovery] {phase} {index}/{total} — {label}    ",
                    err=True,
                    nl=False,
                ),
            )
            typer.echo("", err=True)
            entities_path.parent.mkdir(parents=True, exist_ok=True)
            write_entities(entities_path, report)
            summary["entity_discovery"] = format_discovery_summary(report)
            typer.echo(
                f"  wrote {len(report.canonical_entities)} entities to {entities_path}",
                err=True,
            )

            synth_client = _require_openrouter("entity_synthesis")
            if synth_client is not None and report.canonical_entities:
                typer.echo("→ synthesizing canonical pages per entity…", err=True)
                synth_report = synthesize_entities(
                    report.canonical_entities,
                    corpus_root=paths.corpus_root,
                    client=synth_client,
                    progress=lambda *, index, total, slug, result: typer.echo(
                        f"\r  [synthesis] {index}/{total} — {slug}: {result}    ",
                        err=True,
                        nl=False,
                    ),
                )
                typer.echo("", err=True)
                summary["entity_synthesis"] = format_synthesis_summary(synth_report)

    if do_promote_ideas:
        promote_client = _require_openrouter("idea_promotion")
        if promote_client is not None:
            loaded_entities = load_entities_from_json(entities_path) if entities_path.exists() else []
            typer.echo("→ classifying and promoting ideas…", err=True)
            promote_report = promote_ideas(
                paths.corpus_root,
                loaded_entities,
                client=promote_client,
                progress=lambda *, index, total, label: typer.echo(
                    f"\r  [promote-ideas] {index}/{total} — {label}    ",
                    err=True,
                    nl=False,
                ),
            )
            typer.echo("", err=True)
            summary["idea_promotion"] = format_promotion_summary(promote_report)

    if do_reindex:
        typer.echo(f"→ reindexing corpus at {paths.index_root}…", err=True)
        try:
            reindex_result = reindex_corpus(
                paths.corpus_root,
                paths.index_root,
                build_runtime_embedder(),
            )
            summary["reindex"] = {
                "files_indexed": reindex_result.files_indexed,
                "chunks_indexed": reindex_result.chunks_indexed,
                "vectors_indexed": reindex_result.vectors_indexed,
            }
            typer.echo(
                f"  indexed {reindex_result.files_indexed} files, {reindex_result.chunks_indexed} chunks",
                err=True,
            )
        except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
            summary["reindex_error"] = str(err)
            typer.echo(f"  reindex failed: {err}", err=True)

    if do_mine:
        mine_client = build_openrouter_client(settings, purpose="dream")
        if mine_client is None:
            summary["mine_digests_error"] = "OpenRouter API key missing"
            typer.echo("  mine-digests skipped: no OpenRouter key", err=True)
        else:
            from dory_core.claim_store import ClaimStore as _ClaimStore
            from dory_core.digest_mining import (
                OpenRouterDigestExtractor,
                format_mining_summary,
                mine_digest_tree,
            )

            typer.echo("→ mining digests (this makes LLM calls)…", err=True)
            extractor = OpenRouterDigestExtractor(client=mine_client)
            claim_store = _ClaimStore(paths.corpus_root / ".dory" / "claim-store.db")
            mine_results = mine_digest_tree(
                paths.corpus_root,
                extractor=extractor,
                claim_store=claim_store,
            )
            mine_summary = format_mining_summary(mine_results)
            summary["mine_digests"] = mine_summary
            typer.echo(
                f"  {mine_summary['total_claims_stored']} claims stored "
                f"from {mine_summary['files_with_claims']} digest file(s)",
                err=True,
            )

    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command("mine-digests")
def mine_digests_command(
    ctx: typer.Context,
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Single digest file (corpus-relative or absolute). Mines one file and returns.",
    ),
    since: str | None = typer.Option(None, "--since", help="Only mine digests dated at or after YYYY-MM-DD"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Process at most N digest files"),
    include_weekly: bool = typer.Option(True, "--weekly/--no-weekly", help="Include weekly digests in the scan"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Extract claims but do not store them"),
) -> None:
    """Extract durable claims from daily/weekly digests into the claim store.

    Walks ``logs/daily/``, ``digests/daily/``, and (optionally)
    ``logs/weekly/`` + ``digests/weekly/`` under the corpus root. For
    each digest, calls the LLM to extract structured claims and stores
    them with evidence back to the digest file.

    Requires an OpenRouter API key (set ``DORY_OPENROUTER_API_KEY`` or
    ``OPENROUTER_API_KEY``).
    """
    config = _get_config(ctx)
    settings = DorySettings()
    client = build_openrouter_client(settings, purpose="dream")
    if client is None:
        _fail_with_runtime_error(
            "digest mining requires an OpenRouter API key (set DORY_OPENROUTER_API_KEY or OPENROUTER_API_KEY)."
        )
    extractor = OpenRouterDigestExtractor(client=client)
    store: ClaimStore | None = None
    if not dry_run:
        store = ClaimStore(config.corpus_root / ".dory" / "claim-store.db")

    if path is not None:
        relative = path if not path.is_absolute() else path.relative_to(config.corpus_root)
        result = mine_digest_file(
            relative,
            corpus_root=config.corpus_root,
            extractor=extractor,
            claim_store=store,
            dry_run=dry_run,
        )
        typer.echo(
            json.dumps(
                {
                    "digest_path": result.digest_path,
                    "claims_extracted": result.claims_extracted,
                    "claims_stored": result.claims_stored,
                    "errors": result.errors,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    results = mine_digest_tree(
        config.corpus_root,
        extractor=extractor,
        claim_store=store,
        dry_run=dry_run,
        since=since,
        limit=limit,
        include_weekly=include_weekly,
    )
    summary = format_mining_summary(results)
    summary["dry_run"] = dry_run
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command("migrate-manifest")
def migrate_manifest(
    source_root: Path = typer.Argument(..., help="Path to the legacy memory root to route"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write manifest JSON to this file instead of stdout",
    ),
    summary_only: bool = typer.Option(
        False,
        "--summary-only",
        help="Print only the summary (by_kind / by_destination_bucket)",
    ),
) -> None:
    """Build a dry-run routing manifest for a legacy memory source tree.

    The manifest contains, for every markdown file under ``source_root``,
    either the destination path under the Dory corpus structure or a
    reason it was excluded or flagged for LLM review. Nothing is written
    to the corpus.
    """
    if not source_root.exists():
        _fail_with_runtime_error(f"source root does not exist: {source_root}")
    manifest = build_manifest(source_root)
    payload = manifest["summary"] if summary_only else manifest
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        typer.echo(f"wrote manifest to {output}")
    else:
        typer.echo(rendered)


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    limit: int = typer.Option(10, "-n", "--limit"),
    corpus: str = typer.Option("durable", "--corpus"),
    mode: str = typer.Option("hybrid", "--mode"),
    types: list[str] = typer.Option([], "--type"),
    statuses: list[str] = typer.Option([], "--status"),
    tags: list[str] = typer.Option([], "--tag"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    config = _get_config(ctx)
    try:
        settings = DorySettings()
        planner = _build_retrieval_planner(settings, purpose="query")
        engine = SearchEngine(
            config.index_root,
            build_runtime_embedder(),
            query_expander=_build_query_expander(settings),
            retrieval_planner=planner,
            result_selector=planner,
            reranker=build_reranker(settings),
            rerank_candidate_limit=settings.query_reranker_candidate_limit,
        )
        resp = engine.search(
            SearchReq(
                query=query,
                k=limit,
                corpus=corpus,
                mode=mode,
                scope=SearchScope(type=types, status=statuses, tags=tags),
                debug=debug,
            )
        )
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))
    typer.echo(json.dumps(serialize_search_response(resp, debug=debug), indent=2, sort_keys=True))


@app.command()
def get(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    from_line: int = typer.Option(1, "--from"),
    limit: int | None = typer.Option(None, "-n", "--lines"),
) -> None:
    config = _get_config(ctx)
    target = _resolve_corpus_path(config.corpus_root, path)
    text = target.read_text(encoding="utf-8")
    typer.echo(_slice_lines(text, from_line, limit))


@app.command()
def status(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    typer.echo(format_status(build_status(config.corpus_root, config.index_root)))


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "--"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _make_progress_printer(*, tty: bool) -> Callable[[ReindexProgress], None]:
    last_phase: dict[str, str] = {"value": ""}

    def render(progress: ReindexProgress) -> None:
        total = progress.total if progress.total > 0 else "?"
        percent = ""
        if progress.total > 0:
            percent = f" ({progress.processed * 100 // progress.total}%)"
        rate = f" {progress.rate:.1f}/s" if progress.rate else ""
        eta = f" eta {_format_duration(progress.eta_s)}" if progress.eta_s else ""
        elapsed = f" [{_format_duration(progress.elapsed_s)}]"
        line = (
            f"[reindex] {progress.phase} {progress.processed}/{total}{percent}"
            f"{rate}{eta}{elapsed} {progress.message}"
        )
        stream = sys.stderr
        if tty and progress.phase not in {"done", "plan"}:
            if last_phase["value"] and last_phase["value"] != progress.phase:
                stream.write("\n")
            stream.write("\r\x1b[2K" + line)
            stream.flush()
        else:
            if tty and last_phase["value"] and last_phase["value"] != progress.phase:
                stream.write("\n")
            stream.write(line + "\n")
            stream.flush()
        last_phase["value"] = progress.phase

    return render


def _format_plan(plan: ReconcilePlan) -> str:
    lines = [
        "Reconcile plan:",
        f"  new:       {len(plan.new_paths)}",
        f"  changed:   {len(plan.changed_paths)}",
        f"  orphans:   {len(plan.orphan_paths)}",
        f"  unchanged: {plan.unchanged_count}",
    ]
    if plan.embedding_model_changed:
        lines.append("  model:     embedding model changed — full rebuild required")
    return "\n".join(lines)


def _format_session_plan(session_plan: object) -> str:
    return (
        "Session plane:\n"
        f"  files:     {session_plan.session_files}\n"
        f"  indexed:   {session_plan.session_docs_indexed}\n"
        f"  missing:   {session_plan.missing_docs}\n"
        f"  stale:     {session_plan.stale_docs}"
    )


@app.command()
def reindex(
    ctx: typer.Context,
    plan: bool = typer.Option(
        False, "--plan", help="Print the reconcile plan without touching the index."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a full rebuild (preserves the DB file but replaces every row)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Deprecated alias for --rebuild."
    ),
    batch_size: int = typer.Option(
        200, "--batch-size", min=1, help="Files per reconcile batch (smaller = finer resume granularity)."
    ),
    progress: bool = typer.Option(
        True, "--progress/--no-progress", help="Print reindex progress to stderr."
    ),
) -> None:
    config = _get_config(ctx)
    try:
        embedder = build_runtime_embedder()
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))

    if plan:
        reconcile_plan = plan_reconcile(config.corpus_root, config.index_root, embedder)
        session_plan = plan_session_sync(config.corpus_root, config.index_root / "session_plane.db")
        typer.echo(_format_plan(reconcile_plan), err=True)
        typer.echo(_format_session_plan(session_plan), err=True)
        payload = asdict(reconcile_plan)
        payload["session_plane"] = asdict(session_plan)
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if force:
        typer.echo(
            "[reindex] --force is deprecated; use --rebuild (no directory wipe needed).",
            err=True,
        )
        rebuild = True

    progress_callback = (
        _make_progress_printer(tty=sys.stderr.isatty()) if progress else None
    )

    try:
        if rebuild:
            result = reindex_corpus(
                config.corpus_root,
                config.index_root,
                embedder,
                progress=progress_callback,
            )
            payload = asdict(result)
            payload["session_sync"] = asdict(
                sync_session_files(config.corpus_root, config.index_root / "session_plane.db")
            )
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
            return

        reconcile_result = reconcile_corpus(
            config.corpus_root,
            config.index_root,
            embedder,
            batch_size=batch_size,
            progress=progress_callback,
        )
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))
    payload = asdict(reconcile_result)
    payload["session_sync"] = asdict(sync_session_files(config.corpus_root, config.index_root / "session_plane.db"))
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command()
def neighbors(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    direction: str = typer.Option("out", "--direction"),
    depth: int = typer.Option(1, "--depth"),
    max_edges: int = typer.Option(40, "--max-edges"),
    exclude_prefix: list[str] | None = typer.Option(None, "--exclude-prefix"),
) -> None:
    config = _get_config(ctx)
    result = LinkService(config.corpus_root, config.index_root).neighbors(
        path,
        direction=direction,
        depth=depth,
        max_edges=max_edges,
        exclude_prefixes=exclude_prefix or (),
    )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command()
def backlinks(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    max_edges: int = typer.Option(40, "--max-edges"),
    exclude_prefix: list[str] | None = typer.Option(None, "--exclude-prefix"),
) -> None:
    config = _get_config(ctx)
    result = LinkService(config.corpus_root, config.index_root).backlinks(
        path,
        max_edges=max_edges,
        exclude_prefixes=exclude_prefix or (),
    )
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command()
def lint(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    result = LinkService(config.corpus_root, config.index_root).lint()
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@auth_app.command("new")
def auth_new(
    ctx: typer.Context,
    name: str = typer.Argument(...),
) -> None:
    config = _get_config(ctx)
    token = issue_token(name, config.auth_tokens_path)
    typer.echo(token)


@dream_app.command("list")
def dream_list(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    proposals = list_proposals(config.corpus_root)
    typer.echo(json.dumps({"count": len(proposals), "proposals": proposals}, indent=2))


@dream_app.command("apply")
def dream_apply(
    ctx: typer.Context,
    proposal_id: str = typer.Argument(...),
) -> None:
    config = _get_config(ctx)
    proposal_path = _resolve_proposal_path(config.corpus_root, proposal_id)
    proposal = load_proposal(proposal_path)
    try:
        engine = SemanticWriteEngine(
            root=config.corpus_root,
            index_root=config.index_root,
            embedder=build_runtime_embedder(),
        )
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))
    applied_targets: list[str] = []
    for action in proposal.actions:
        response = engine.write(
            MemoryWriteReq(
                action=action.action,
                kind=action.kind,
                subject=action.subject,
                content=action.content,
                scope=action.scope,
                confidence=action.confidence,
                reason=action.reason,
                source=action.source,
                soft=action.soft,
                allow_canonical=True,
            )
        )
        if response.result in {"rejected", "quarantined"}:
            raise typer.BadParameter(response.message or f"proposal action failed for subject {action.subject}")
        applied_targets.append(response.target_path or response.subject_ref or action.subject)

    applied_root = config.corpus_root / "inbox" / "applied"
    applied_root.mkdir(parents=True, exist_ok=True)
    applied_path = applied_root / proposal_path.name
    applied_path.write_text(proposal_path.read_text(encoding="utf-8"), encoding="utf-8")
    proposal_path.unlink()
    typer.echo(json.dumps({"applied": applied_targets}, indent=2))


@dream_app.command("distill")
def dream_distill(
    ctx: typer.Context,
    session_path: str = typer.Argument(..., help="Corpus-relative session markdown path"),
    agent: str | None = typer.Option(None, "--agent", help="Override agent name"),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_llm = require_dream_llm(settings)
    session_file = _resolve_corpus_path(config.corpus_root, session_path)
    resolved_agent = agent or _infer_agent_from_session_path(session_path)
    event = SessionClosedEvent.now(agent=resolved_agent, session_path=session_path)
    distiller = OpenRouterSessionDistiller(client=dream_llm.client, writer=DistillationWriter(config.corpus_root))
    target = distiller.distill(event, session_file.read_text(encoding="utf-8"))
    typer.echo(str(target.relative_to(config.corpus_root)))


@dream_app.command("propose")
def dream_propose(
    ctx: typer.Context,
    distilled_id: str = typer.Argument(..., help="Distilled note id or corpus-relative path"),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_llm = require_dream_llm(settings)
    distilled_path = _resolve_distilled_path(config.corpus_root, distilled_id)
    generator = ProposalGenerator(
        root=config.corpus_root,
        backend=dream_llm.backend,
        client=dream_llm.client,
    )
    target = generator.generate(distilled_path)
    typer.echo(str(target.relative_to(config.corpus_root)))


@dream_app.command("reject")
def dream_reject(
    ctx: typer.Context,
    proposal_id: str = typer.Argument(...),
) -> None:
    config = _get_config(ctx)
    proposal_path = _resolve_proposal_path(config.corpus_root, proposal_id)
    rejected_root = config.corpus_root / "inbox" / "rejected"
    rejected_root.mkdir(parents=True, exist_ok=True)
    target = rejected_root / proposal_path.name
    target.write_text(proposal_path.read_text(encoding="utf-8"), encoding="utf-8")
    proposal_path.unlink()
    typer.echo(str(target.relative_to(config.corpus_root)))


@maintain_app.command("inspect")
def maintain_inspect(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Corpus-relative markdown path"),
    write_report: bool = typer.Option(False, "--write-report", help="Persist report under inbox/maintenance"),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    client = _require_openrouter_client(settings, purpose="maintenance")
    target = _resolve_corpus_path(config.corpus_root, path)
    inspector = OpenRouterMaintenanceInspector(client=client)
    report = inspector.inspect(path, target.read_text(encoding="utf-8"))
    payload = asdict(report)
    if write_report:
        payload["report_path"] = str(
            MaintenanceReportWriter(config.corpus_root).write(report).relative_to(config.corpus_root)
        )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@maintain_app.command("wiki-health")
def maintain_wiki_health(
    ctx: typer.Context,
    write_report: bool = typer.Option(False, "--write-report", help="Persist report under inbox/maintenance"),
) -> None:
    config = _get_config(ctx)
    payload = WikiHealthRunner(config.corpus_root).run(write_report=write_report)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@maintain_app.command("backfill-privacy-metadata")
def maintain_backfill_privacy_metadata(
    ctx: typer.Context,
    path: list[str] = typer.Option([], "--path", help="Limit to a corpus-relative markdown path. Repeatable."),
    refresh: bool = typer.Option(False, "--refresh", help="Refresh wiki-health before planning paths."),
    apply: bool = typer.Option(False, "--apply", help="Write changes. Default is dry-run only."),
) -> None:
    config = _get_config(ctx)
    result = PrivacyMetadataBackfiller(config.corpus_root).run(
        paths=path or None,
        dry_run=not apply,
        refresh=refresh,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@ops_app.command("dream-once")
def ops_dream_once(
    ctx: typer.Context,
    session: list[str] = typer.Option(
        [],
        "--session",
        help="Explicit legacy path: distill these raw session paths before proposing. Defaults to digest/recall sources.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Process at most N session distillations and N proposal generations.",
    ),
    min_age_minutes: float = typer.Option(
        0,
        "--min-age-minutes",
        min=0,
        help="Skip session files modified more recently than this many minutes.",
    ),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_llm = require_dream_llm(settings)
    result = DreamOnceRunner(
        config.corpus_root,
        dream_llm.client,
        index_root=config.index_root,
        backend=dream_llm.backend,
    ).run(
        session_paths=session or None,
        limit=limit,
        min_session_age_seconds=min_age_minutes * 60,
    )
    typer.echo(serialize_result(result))


@ops_app.command("daily-digest-once")
def ops_daily_digest_once(
    ctx: typer.Context,
    digest_date: str | None = typer.Option(
        None,
        "--date",
        help="Digest date as YYYY-MM-DD. Defaults to yesterday; pass --today for today's sessions.",
    ),
    today: bool = typer.Option(False, "--today", help="Digest today's sessions instead of yesterday."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing daily digest for the date."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate and print without writing."),
    reindex: bool = typer.Option(True, "--reindex/--no-reindex", help="Reindex the written digest path."),
    min_age_minutes: float = typer.Option(
        30,
        "--min-age-minutes",
        min=0,
        help="Skip session files modified more recently than this many minutes.",
    ),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Process at most N sessions for the day."),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_llm = require_dream_llm(settings)
    target_date = date.today().isoformat() if today else digest_date or previous_day()
    result = DailyDigestWriter(
        config.corpus_root,
        OpenRouterDailyDigestGenerator(client=dream_llm.client),
    ).write(
        target_date=target_date,
        overwrite=overwrite,
        dry_run=dry_run,
        min_session_age_seconds=min_age_minutes * 60,
        limit=limit,
    )
    payload = asdict(result)
    if result.written and reindex:
        try:
            reindex_result = reindex_paths(
                config.corpus_root,
                config.index_root,
                build_runtime_embedder(),
                [result.digest_path],
            )
        except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
            _fail_with_runtime_error(str(err))
        payload["reindex"] = asdict(reindex_result)
        payload["reindexed"] = True
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@ops_app.command("weekly-digest-once")
def ops_weekly_digest_once(
    ctx: typer.Context,
    week: str | None = typer.Option(
        None,
        "--week",
        help="ISO week as YYYY-Www. Defaults to previous week; pass --current-week for the current week.",
    ),
    current_week: bool = typer.Option(False, "--current-week", help="Digest the current ISO week instead of previous week."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing weekly digest for the week."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate and print without writing."),
    reindex: bool = typer.Option(True, "--reindex/--no-reindex", help="Reindex the written digest path."),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_llm = require_dream_llm(settings)
    target_week = current_iso_week() if current_week else week or previous_iso_week()
    result = WeeklyDigestWriter(
        config.corpus_root,
        OpenRouterWeeklyDigestGenerator(client=dream_llm.client),
    ).write(
        week=target_week,
        overwrite=overwrite,
        dry_run=dry_run,
    )
    payload = asdict(result)
    if result.written and reindex:
        try:
            reindex_result = reindex_paths(
                config.corpus_root,
                config.index_root,
                build_runtime_embedder(),
                [result.digest_path],
            )
        except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
            _fail_with_runtime_error(str(err))
        payload["reindex"] = asdict(reindex_result)
        payload["reindexed"] = True
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@ops_app.command("maintain-once")
def ops_maintain_once(
    ctx: typer.Context,
    path: list[str] = typer.Option([], "--path", help="Limit to specific corpus-relative paths"),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    client = _require_openrouter_client(settings, purpose="maintenance")
    result = MaintenanceOnceRunner(config.corpus_root, client).run(targets=path or None)
    typer.echo(serialize_result(result))


@ops_app.command("wiki-health")
def ops_wiki_health(
    ctx: typer.Context,
    write_report: bool = typer.Option(False, "--write-report", help="Persist report under inbox/maintenance"),
) -> None:
    config = _get_config(ctx)
    payload = WikiHealthRunner(config.corpus_root).run(write_report=write_report)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@ops_app.command("wiki-refresh-once")
def ops_wiki_refresh_once(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    written = run_compiled_wiki_refresh(config.corpus_root)
    typer.echo(json.dumps({"written": written}, indent=2, sort_keys=True))


@ops_app.command("wiki-refresh-indexes")
def ops_wiki_refresh_indexes(ctx: typer.Context) -> None:
    config = _get_config(ctx)
    written = run_wiki_index_refresh(config.corpus_root)
    typer.echo(json.dumps({"written": written}, indent=2, sort_keys=True))


@ops_app.command("eval-once")
def ops_eval_once(
    ctx: typer.Context,
    reindex_first: bool = typer.Option(True, "--reindex/--no-reindex"),
    questions_root: Path = typer.Option(Path("eval/public/questions"), "--questions-root"),
    runs_root: Path = typer.Option(Path("eval/runs"), "--runs-root"),
    top_k: int = typer.Option(5, "--top-k"),
) -> None:
    config = _get_config(ctx)
    try:
        runner = EvalOnceRunner(config.corpus_root, config.index_root, build_runtime_embedder())
        result = runner.run(
            reindex_first=reindex_first,
            questions_root=questions_root,
            runs_root=runs_root,
            top_k=top_k,
        )
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))
    typer.echo(serialize_result(result))


@ops_app.command("watch")
def ops_watch(
    ctx: typer.Context,
    debounce_seconds: float = typer.Option(1.0, "--debounce-seconds"),
    dream: bool = typer.Option(True, "--dream/--no-dream"),
    poll_interval: float = typer.Option(0.25, "--poll-interval"),
) -> None:
    config = _get_config(ctx)
    settings = DorySettings()
    dream_runner = None
    dream_enabled = False
    dream_warning: str | None = None
    if dream:
        dream_llm = build_dream_llm(settings)
        if dream_llm is None:
            dream_warning = (
                "dream mode disabled: no dream LLM is configured. "
                "Set DORY_DREAM_LLM_PROVIDER=local with DORY_LOCAL_LLM_* or configure OpenRouter."
            )
        else:
            dream_runner = DreamOnceRunner(
                config.corpus_root,
                dream_llm.client,
                index_root=config.index_root,
                backend=dream_llm.backend,
            )
            dream_enabled = True
    try:
        runner = OpsWatchRunner(
            corpus_root=config.corpus_root,
            index_root=config.index_root,
            embedder=build_runtime_embedder(),
            debounce_seconds=debounce_seconds,
            dream_runner=dream_runner,
        )
    except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
        _fail_with_runtime_error(str(err))

    typer.echo(
        json.dumps(
            {
                "watching": str(config.corpus_root),
                "debounce_seconds": debounce_seconds,
                "dream": dream_enabled,
                "dream_requested": dream,
                "warning": dream_warning,
            },
            indent=2,
            sort_keys=True,
        )
    )
    runner.serve_forever(poll_interval=poll_interval)



if __name__ == "__main__":
    app()
