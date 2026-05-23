"""Proposals command group for Dory CLI — create, review, apply, reject memory proposals."""
from __future__ import annotations

import json
from dataclasses import asdict

import typer

from dory_cli._internals import (
    _build_semantic_write_engine,
    _fail_with_runtime_error,
    _get_config,
)
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError
from dory_core.errors import DoryValidationError
from dory_core.dreaming.proposals import (
    ProposalStore,
    apply_proposal,
    create_semantic_write_proposal,
    proposal_to_payload,
    reject_proposal,
)
from dory_core.types import (
    MemoryProposalApplyReq,
    MemoryProposalCreateReq,
    MemoryProposalGetReq,
    MemoryProposalListReq,
    MemoryProposalRejectReq,
)


def register(proposals_app: typer.Typer) -> None:
    """Register proposals commands on the given Typer sub-app."""

    @proposals_app.command("create")
    def proposals_create(
        ctx: typer.Context,
        content: str = typer.Argument(..., help="Memory content to propose"),
        subject: str = typer.Option(..., "--subject", help="Fuzzy subject to route the memory to"),
        action: str = typer.Option("write", "--action", help="Semantic write action"),
        kind: str = typer.Option("fact", "--kind", help="Semantic memory kind"),
        scope: str | None = typer.Option(None, "--scope", help="Optional routing scope"),
        confidence: str | None = typer.Option(None, "--confidence", help="Optional confidence hint"),
        reason: str | None = typer.Option(None, "--reason", help="Optional reason or context"),
        source: str | None = typer.Option(None, "--source", help="Optional source label"),
        agent: str | None = typer.Option(None, "--agent", help="Optional agent identity for provenance"),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional session id for provenance"),
        origin_surface: str | None = typer.Option(
            None, "--origin-surface", help="Optional client/tool provenance label"
        ),
        source_paths: list[str] = typer.Option([], "--source-path", help="Evidence/source path for review"),
        proposal_id: str | None = typer.Option(None, "--proposal-id", help="Optional stable proposal id"),
        soft: bool = typer.Option(False, "--soft/--no-soft", help="Quarantine instead of rejecting on ambiguity"),
        force_inbox: bool = typer.Option(
            False, "--force-inbox/--no-force-inbox", help="Capture under inbox/semantic"
        ),
    ) -> None:
        config = _get_config(ctx)
        req = MemoryProposalCreateReq.model_validate(
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
                "force_inbox": force_inbox,
                "agent": agent,
                "session_id": session_id,
                "origin_surface": origin_surface,
                "source_paths": source_paths,
                "proposal_id": proposal_id,
            }
        )
        try:
            proposal, path = create_semantic_write_proposal(
                root=config.corpus_root,
                engine=_build_semantic_write_engine(config),
                req=req,
            )
        except (DoryValidationError, EmbeddingConfigurationError, EmbeddingProviderError) as err:
            _fail_with_runtime_error(str(err))
        typer.echo(
            json.dumps(
                {
                    "proposal_id": proposal.proposal_id,
                    "path": path.relative_to(config.corpus_root).as_posix(),
                    "proposal": proposal_to_payload(proposal),
                },
                indent=2,
                sort_keys=True,
            )
        )

    @proposals_app.command("list")
    def proposals_list(
        ctx: typer.Context,
        status: str = typer.Option("pending", "--status", help="pending, applied, or rejected"),
    ) -> None:
        config = _get_config(ctx)
        req = MemoryProposalListReq.model_validate({"status": status})
        proposals = ProposalStore(config.corpus_root).list(status=req.status)
        typer.echo(
            json.dumps({"count": len(proposals), "proposals": proposals, "status": req.status}, indent=2)
        )

    @proposals_app.command("show")
    def proposals_show(
        ctx: typer.Context,
        proposal_id: str = typer.Argument(...),
        status: str = typer.Option("pending", "--status", help="pending, applied, or rejected"),
    ) -> None:
        config = _get_config(ctx)
        req = MemoryProposalGetReq.model_validate({"proposal_id": proposal_id, "status": status})
        try:
            proposal = ProposalStore(config.corpus_root).load(req.proposal_id, status=req.status)
        except DoryValidationError as err:
            _fail_with_runtime_error(str(err))
        typer.echo(json.dumps(proposal_to_payload(proposal), indent=2, sort_keys=True))

    @proposals_app.command("apply")
    def proposals_apply(
        ctx: typer.Context,
        proposal_id: str = typer.Argument(...),
        agent: str | None = typer.Option(None, "--agent", help="Optional applying agent"),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional applying session id"),
        origin_surface: str | None = typer.Option(None, "--origin-surface", help="Optional applying surface"),
    ) -> None:
        config = _get_config(ctx)
        req = MemoryProposalApplyReq(
            proposal_id=proposal_id, agent=agent, session_id=session_id, origin_surface=origin_surface
        )
        try:
            result = apply_proposal(
                root=config.corpus_root,
                engine=_build_semantic_write_engine(config),
                proposal_id=req.proposal_id,
                agent=req.agent,
                session_id=req.session_id,
                origin_surface=req.origin_surface,
            )
        except (DoryValidationError, EmbeddingConfigurationError, EmbeddingProviderError) as err:
            _fail_with_runtime_error(str(err))
        typer.echo(json.dumps(asdict(result), indent=2, sort_keys=True))

    @proposals_app.command("reject")
    def proposals_reject(
        ctx: typer.Context,
        proposal_id: str = typer.Argument(...),
        reason: str | None = typer.Option(None, "--reason", help="Reason for rejecting the proposal"),
    ) -> None:
        config = _get_config(ctx)
        req = MemoryProposalRejectReq(proposal_id=proposal_id, reason=reason)
        try:
            target = reject_proposal(root=config.corpus_root, proposal_id=req.proposal_id, reason=req.reason)
        except DoryValidationError as err:
            _fail_with_runtime_error(str(err))
        typer.echo(target)
