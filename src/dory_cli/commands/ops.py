"""Ops command group for Dory CLI — operator-first batch jobs and watch loops."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import typer

from dory_cli._internals import (
    _fail_with_runtime_error,
    _get_config,
    _require_openrouter_client,
)
from dory_core.config import DorySettings
from dory_core.digest_writer import (
    DailyDigestWriter,
    OpenRouterDailyDigestGenerator,
    OpenRouterWeeklyDigestGenerator,
    WeeklyDigestWriter,
    current_iso_week,
    previous_day,
    previous_iso_week,
)
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError
from dory_core.index.reindex import reindex_paths
from dory_core.ops import (
    DreamOnceRunner,
    EvalOnceRunner,
    MaintenanceOnceRunner,
    WikiHealthRunner,
    serialize_result,
)
from dory_core.ops import run_compiled_wiki_refresh, run_wiki_index_refresh


def _cli_main_module():
    """Return the live main module for test monkeypatch-compatible shims."""
    return sys.modules.get("dory_cli.main") or sys.modules.get("__main__")


def register(ops_app: typer.Typer) -> None:
    """Register ops commands on the given Typer sub-app."""

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
        # NOTE: require_dream_llm accessed via _cli_main for test monkeypatch compatibility
        dream_llm = _cli_main_module().require_dream_llm(settings)
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
        # NOTE: require_dream_llm accessed via _cli_main for test monkeypatch compatibility
        dream_llm = _cli_main_module().require_dream_llm(settings)
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
                    _cli_main_module().build_runtime_embedder(),
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
        current_week: bool = typer.Option(
            False, "--current-week", help="Digest the current ISO week instead of previous week."
        ),
        overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing weekly digest for the week."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Generate and print without writing."),
        reindex: bool = typer.Option(True, "--reindex/--no-reindex", help="Reindex the written digest path."),
    ) -> None:
        config = _get_config(ctx)
        settings = DorySettings()
        # NOTE: require_dream_llm accessed via _cli_main for test monkeypatch compatibility
        dream_llm = _cli_main_module().require_dream_llm(settings)
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
                    _cli_main_module().build_runtime_embedder(),
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
        corpus_root_override: Path | None = typer.Option(
            None,
            "--corpus-root",
            help="Run the eval against this corpus instead of the configured Dory corpus.",
        ),
        index_root_override: Path | None = typer.Option(
            None,
            "--index-root",
            help="Use this index path for the eval run (defaults to .dory/index next to the corpus).",
        ),
    ) -> None:
        config = _get_config(ctx)
        if corpus_root_override is not None:
            eval_corpus = corpus_root_override
            eval_index = index_root_override or (corpus_root_override.parent / ".dory" / "index")
        else:
            eval_corpus = config.corpus_root
            eval_index = index_root_override or config.index_root
        try:
            runner = EvalOnceRunner(
                eval_corpus,
                eval_index,
                _cli_main_module().build_runtime_embedder(),
            )
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
            # NOTE: build_dream_llm accessed via _cli_main for test monkeypatch compatibility
            dream_llm = _cli_main_module().build_dream_llm(settings)
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
            # NOTE: OpsWatchRunner and build_runtime_embedder accessed via _cli_main
            # for test monkeypatch compatibility
            runner = _cli_main_module().OpsWatchRunner(
                corpus_root=config.corpus_root,
                index_root=config.index_root,
                embedder=_cli_main_module().build_runtime_embedder(),
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
