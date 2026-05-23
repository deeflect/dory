"""Maintain command group for Dory CLI — inspect corpus docs and emit maintenance suggestions."""
from __future__ import annotations

import json
from dataclasses import asdict

import typer

from dory_cli._internals import (
    _get_config,
    _require_openrouter_client,
    _resolve_corpus_path,
)
from dory_core.config import DorySettings
from dory_core.maintenance import MaintenanceReportWriter, OpenRouterMaintenanceInspector
from dory_core.ops import WikiHealthRunner


def register(maintain_app: typer.Typer) -> None:
    """Register maintain commands on the given Typer sub-app."""

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
        from dory_core.maintenance import PrivacyMetadataBackfiller

        result = PrivacyMetadataBackfiller(config.corpus_root).run(
            paths=path or None,
            dry_run=not apply,
            refresh=refresh,
        )
        typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
