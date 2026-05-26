from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

from dory_cli import _internals as cli_internals
from dory_cli._internals import (
    RuntimeConfig,
    _build_active_memory_engine,
    _get_config,
)
from dory_core.embedding import build_runtime_embedder
from dory_core.purge import PurgeEngine
from dory_core.types import ActiveMemoryReq, MemoryWriteReq, PurgeReq, ResearchReq, SearchScope, WakeReq
from dory_core.wake import WakeBuilder


def register(
    app: typer.Typer,
    *,
    active_memory_engine_builder: Callable[[RuntimeConfig], Any] = _build_active_memory_engine,
) -> None:
    @app.command()
    def wake(
        ctx: typer.Context,
        budget: int = typer.Option(600, "--budget"),
        agent: str = typer.Option("codex", "--agent"),
        profile: str = typer.Option("default", "--profile"),
        project: str | None = typer.Option(None, "--project", help="Optional project/entity handle to include in wake."),
        cwd: str | None = typer.Option(None, "--cwd", help="Optional working directory for project inference."),
    ) -> None:
        config = _get_config(ctx)
        resp = WakeBuilder(config.corpus_root).build(
            WakeReq(budget_tokens=budget, agent=agent, profile=profile, project=project, cwd=cwd)
        )
        typer.echo(resp.block)

    @app.command("active-memory")
    def active_memory(
        ctx: typer.Context,
        prompt: str = typer.Argument(...),
        agent: str = typer.Option("codex", "--agent"),
        cwd: str | None = typer.Option(None, "--cwd"),
        project: str | None = typer.Option(None, "--project", help="Optional project/entity handle to include."),
        session_key: str | None = typer.Option(None, "--session-key", help="Optional session key for recall scoping."),
        session_ids: list[str] = typer.Option([], "--session-id", help="Session id filter for recall evidence."),
        session_agents: list[str] = typer.Option([], "--session-agent", help="Session agent filter for recall evidence."),
        devices: list[str] = typer.Option([], "--device", help="Session device filter for recall evidence."),
        session_statuses: list[str] = typer.Option(
            [],
            "--session-status",
            help="Session status filter for recall evidence.",
        ),
        since: str | None = typer.Option(None, "--since", help="Lower updated-time bound for recall evidence."),
        until: str | None = typer.Option(None, "--until", help="Upper updated-time bound for recall evidence."),
        profile: str = typer.Option("auto", "--profile"),
        include_wake: bool = typer.Option(True, "--include-wake/--no-include-wake"),
    ) -> None:
        config = _get_config(ctx)
        result = active_memory_engine_builder(config).build(
            ActiveMemoryReq(
                prompt=prompt,
                agent=agent,
                cwd=cwd,
                project=project,
                scope=SearchScope(
                    agent=session_agents,
                    device=devices,
                    session_id=session_ids,
                    session_key=session_key,
                    status=session_statuses,
                    since=since,
                    until=until,
                ),
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
        agent: str | None = typer.Option(None, "--agent", help="Optional agent identity for provenance"),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional session id for provenance"),
        origin_surface: str | None = typer.Option(None, "--origin-surface", help="Optional client/tool provenance label"),
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
                "agent": agent,
                "session_id": session_id,
                "origin_surface": origin_surface,
                "soft": soft,
                "dry_run": dry_run,
                "force_inbox": force_inbox,
                "allow_canonical": allow_canonical,
            }
        )
        result = cli_internals._build_dory_runtime(config).memory_write(request)
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
        payload = cli_internals._build_dory_runtime(config).research(
            ResearchReq(
                question=question,
                kind=kind,  # type: ignore[arg-type]
                corpus=corpus,  # type: ignore[arg-type]
                limit=limit,
                save=save,
            )
        )
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
