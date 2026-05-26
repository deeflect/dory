from __future__ import annotations

import json
import sys
from dataclasses import asdict
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
    _build_retrieval_planner as _build_retrieval_planner,  # noqa: F401 - compatibility for tests/patching
    _fail_with_runtime_error,
    _get_config,
    _init_directories,
    _init_seed_documents,
    _planner_with_pricing_overrides,
    _run_interactive_migration_plan,
    _serialize_migration_plan,
)
from dory_cli.eval import app as eval_app
from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError, build_runtime_embedder
from dory_core.index.reindex import reindex_corpus
from dory_core.llm.dream import (
    build_dream_llm as build_dream_llm,  # noqa: F401 - compatibility for tests/patching
    require_dream_llm as require_dream_llm,  # noqa: F401 - compatibility for tests/patching
)
from dory_core.llm_rerank import build_reranker as build_reranker  # noqa: F401 - compatibility for tests/patching
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client
from dory_core.ops import OpsWatchRunner as OpsWatchRunner  # noqa: F401 - compatibility for tests/patching
from dory_core.migration_source_router import build_manifest, walk_source_tree
from dory_core.migration_executor import execute_manifest, execute_source_tree
from dory_core.migration_review_router import OpenRouterReviewRouter
from dory_core.migration_core_seed import format_seed_summary, seed_core_from_root
from dory_core.migration_batching import build_batches, format_batching_summary
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
from dory_core.claim_store import ClaimStore
from dory_core.digest_mining import (
    OpenRouterDigestExtractor,
    format_mining_summary,
    mine_digest_file,
    mine_digest_tree,
)

app = typer.Typer(add_completion=False, help="Dory CLI")
app.add_typer(eval_app, name="eval")
auth_app = typer.Typer(add_completion=False, help="Manage bearer tokens.")
app.add_typer(auth_app, name="auth")
dream_app = typer.Typer(add_completion=False, help="Review and apply dreaming proposals.")
app.add_typer(dream_app, name="dream")
proposals_app = typer.Typer(add_completion=False, help="Create, review, apply, and reject memory proposals.")
app.add_typer(proposals_app, name="proposals")
maintain_app = typer.Typer(add_completion=False, help="Inspect corpus docs and emit maintenance suggestions.")
app.add_typer(maintain_app, name="maintain")
ops_app = typer.Typer(add_completion=False, help="Operator-first batch jobs and watch loops.")
app.add_typer(ops_app, name="ops")

# Register subcommand groups from external command modules.
from dory_cli.commands.auth import register as _register_auth  # noqa: E402
from dory_cli.commands.core import register as _register_core  # noqa: E402
from dory_cli.commands.dream import register as _register_dream  # noqa: E402
from dory_cli.commands.maintain import register as _register_maintain  # noqa: E402
from dory_cli.commands.memory import register as _register_memory  # noqa: E402
from dory_cli.commands.ops import register as _register_ops  # noqa: E402
from dory_cli.commands.proposals import register as _register_proposals  # noqa: E402

_register_auth(auth_app)
_register_core(app)
_register_dream(dream_app)
_register_maintain(maintain_app)
_register_memory(app, active_memory_engine_builder=lambda config: _build_active_memory_engine(config))
_register_ops(ops_app)
_register_proposals(proposals_app)


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


if __name__ == "__main__":
    app()
