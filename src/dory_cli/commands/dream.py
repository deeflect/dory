"""Dream command group for Dory CLI — review and apply dreaming proposals."""
from __future__ import annotations

import json
import sys

import typer

from dory_cli._internals import (
    _build_semantic_write_engine,
    _fail_with_runtime_error,
    _get_config,
    _infer_agent_from_session_path,
    _resolve_corpus_path,
    _resolve_distilled_path,
)
from dory_core.config import DorySettings
from dory_core.dreaming.events import SessionClosedEvent
from dory_core.dreaming.extract import DistillationWriter, OpenRouterSessionDistiller
from dory_core.dreaming.proposals import (
    ProposalGenerator,
    apply_proposal,
    list_proposals,
    reject_proposal,
)
from dory_core.errors import DoryValidationError
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError


def _cli_main_module():
    """Return the live main module for test monkeypatch-compatible shims."""
    return sys.modules.get("dory_cli.main") or sys.modules.get("__main__")


def register(dream_app: typer.Typer) -> None:
    """Register dream commands on the given Typer sub-app."""

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
        try:
            result = apply_proposal(
                root=config.corpus_root,
                engine=_build_semantic_write_engine(config),
                proposal_id=proposal_id,
            )
        except (DoryValidationError, EmbeddingConfigurationError, EmbeddingProviderError) as err:
            _fail_with_runtime_error(str(err))
        typer.echo(json.dumps({"applied": list(result.applied)}, indent=2))

    @dream_app.command("distill")
    def dream_distill(
        ctx: typer.Context,
        session_path: str = typer.Argument(..., help="Corpus-relative session markdown path"),
        agent: str | None = typer.Option(None, "--agent", help="Override agent name"),
    ) -> None:
        config = _get_config(ctx)
        settings = DorySettings()
        # NOTE: require_dream_llm accessed via _cli_main for test monkeypatch compatibility
        dream_llm = _cli_main_module().require_dream_llm(settings)
        session_file = _resolve_corpus_path(config.corpus_root, session_path)
        resolved_agent = agent or _infer_agent_from_session_path(session_path)
        event = SessionClosedEvent.now(agent=resolved_agent, session_path=session_path)
        distiller = OpenRouterSessionDistiller(
            client=dream_llm.client, writer=DistillationWriter(config.corpus_root)
        )
        target = distiller.distill(event, session_file.read_text(encoding="utf-8"))
        typer.echo(str(target.relative_to(config.corpus_root)))

    @dream_app.command("propose")
    def dream_propose(
        ctx: typer.Context,
        distilled_id: str = typer.Argument(..., help="Distilled note id or corpus-relative path"),
    ) -> None:
        config = _get_config(ctx)
        settings = DorySettings()
        # NOTE: require_dream_llm accessed via _cli_main for test monkeypatch compatibility
        dream_llm = _cli_main_module().require_dream_llm(settings)
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
        try:
            target = reject_proposal(root=config.corpus_root, proposal_id=proposal_id)
        except DoryValidationError as err:
            _fail_with_runtime_error(str(err))
        typer.echo(target)
