from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict

import typer

from dory_cli import _internals as cli_internals
from dory_cli._internals import _fail_with_runtime_error, _get_config, _resolve_corpus_path, _slice_lines
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError
from dory_core.index.reindex import ReconcilePlan, ReindexProgress, plan_reconcile, reconcile_corpus, reindex_corpus
from dory_core.link import LinkService
from dory_core.session_sync import plan_session_sync, sync_session_files
from dory_core.status import build_status, format_status
from dory_core.types import SearchReq, SearchScope, serialize_search_response


def register(app: typer.Typer) -> None:
    @app.command()
    def search(
        ctx: typer.Context,
        query: str = typer.Argument(...),
        limit: int = typer.Option(10, "-n", "--limit"),
        corpus: str = typer.Option("durable", "--corpus"),
        mode: str = typer.Option("hybrid", "--mode"),
        path_glob: str | None = typer.Option(None, "--path-glob"),
        types: list[str] = typer.Option([], "--type"),
        statuses: list[str] = typer.Option([], "--status"),
        tags: list[str] = typer.Option([], "--tag"),
        agents: list[str] = typer.Option([], "--agent"),
        devices: list[str] = typer.Option([], "--device"),
        session_ids: list[str] = typer.Option([], "--session-id"),
        session_key: str | None = typer.Option(None, "--session-key"),
        since: str | None = typer.Option(None, "--since"),
        until: str | None = typer.Option(None, "--until"),
        debug: bool = typer.Option(False, "--debug"),
    ) -> None:
        config = _get_config(ctx)
        try:
            resp = cli_internals._build_dory_runtime(config).search(
                SearchReq(
                    query=query,
                    k=limit,
                    corpus=corpus,
                    mode=mode,
                    scope=SearchScope(
                        path_glob=path_glob,
                        type=types,
                        status=statuses,
                        tags=tags,
                        agent=agents,
                        device=devices,
                        session_id=session_ids,
                        session_key=session_key,
                        since=since,
                        until=until,
                    ),
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

    @app.command()
    def reindex(
        ctx: typer.Context,
        plan: bool = typer.Option(False, "--plan", help="Print the reconcile plan without touching the index."),
        rebuild: bool = typer.Option(
            False, "--rebuild", help="Force a full rebuild (preserves the DB file but replaces every row)."
        ),
        force: bool = typer.Option(False, "--force", help="Deprecated alias for --rebuild."),
        batch_size: int = typer.Option(
            200, "--batch-size", min=1, help="Files per reconcile batch (smaller = finer resume granularity)."
        ),
        progress: bool = typer.Option(True, "--progress/--no-progress", help="Print reindex progress to stderr."),
    ) -> None:
        config = _get_config(ctx)
        try:
            embedder = cli_internals.build_runtime_embedder()
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

        progress_callback = _make_progress_printer(tty=sys.stderr.isatty()) if progress else None

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
        lines.append("  model:     embedding model changed - full rebuild required")
    return "\n".join(lines)


def _format_session_plan(session_plan: object) -> str:
    return (
        "Session plane:\n"
        f"  files:     {session_plan.session_files}\n"
        f"  indexed:   {session_plan.session_docs_indexed}\n"
        f"  missing:   {session_plan.missing_docs}\n"
        f"  stale:     {session_plan.stale_docs}"
    )
