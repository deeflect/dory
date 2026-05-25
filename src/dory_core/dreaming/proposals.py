from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from dory_core.errors import DoryValidationError
from dory_core.llm.json_client import JSONGenerationClient
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.slug import slugify_path_segment
from dory_core.types import MemoryProposalCreateReq, MemoryWriteAction, MemoryWriteKind, MemoryWriteReq


_PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["write", "replace", "forget"],
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "preference", "state", "decision", "note"],
                    },
                    "subject": {"type": "string"},
                    "content": {"type": "string"},
                    "scope": {
                        "type": ["string", "null"],
                        "enum": ["person", "project", "concept", "decision", "core", None],
                    },
                    "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
                    "reason": {
                        "type": ["string", "null"],
                    },
                    "source": {"type": ["string", "null"]},
                    "soft": {"type": "boolean"},
                },
                "required": ["action", "kind", "subject", "content", "scope", "confidence", "reason", "source", "soft"],
            },
        }
    },
    "required": ["actions"],
}


@dataclass(frozen=True, slots=True)
class ProposalAction:
    action: MemoryWriteAction
    kind: MemoryWriteKind
    subject: str
    content: str
    scope: Literal["person", "project", "concept", "decision", "core"] | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    reason: str | None = None
    source: str | None = None
    soft: bool = False
    force_inbox: bool = False
    dry_run: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProposalDocument:
    proposal_id: str
    source_distilled_path: str
    backend: str
    actions: list[ProposalAction]
    proposal_kind: str = "dream"
    status: Literal["pending", "applied", "rejected"] = "pending"
    created_at: str | None = None
    agent: str | None = None
    session_id: str | None = None
    origin_surface: str | None = None
    reason: str | None = None
    source_paths: list[str] | None = None
    applied_at: str | None = None
    rejected_at: str | None = None
    rejected_reason: str | None = None


class ProposalGenerator:
    def __init__(
        self,
        root: Path,
        backend: str,
        *,
        client: JSONGenerationClient | None = None,
    ) -> None:
        self.root = Path(root)
        self.backend = backend
        self.client = client

    def generate(self, distilled_path: Path) -> Path:
        distilled_path = Path(distilled_path)
        proposal_id = distilled_path.stem
        target = self.root / "inbox" / "proposed" / f"{proposal_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)

        summary = distilled_path.read_text(encoding="utf-8").strip()
        actions = self._materialize_actions(distilled_path=distilled_path, proposal_id=proposal_id, summary=summary)
        proposal = ProposalDocument(
            proposal_id=proposal_id,
            source_distilled_path=str(distilled_path),
            backend=self.backend,
            actions=actions,
            created_at=_utc_now(),
            source_paths=[str(distilled_path)],
        )
        _write_json(target, proposal_to_payload(proposal))
        return target

    def _materialize_actions(
        self,
        *,
        distilled_path: Path,
        proposal_id: str,
        summary: str,
    ) -> list[ProposalAction]:
        if self.client is None:
            return []

        payload = self.client.generate_json(
            system_prompt=(
                "You convert a Dory digest or distilled note into conservative reviewable semantic-memory proposals. "
                "Use only facts present in the source note. Do not infer, backfill, or use outside knowledge. "
                "Emit only durable memory: stable project state, explicit decisions, preferences, "
                "operational configuration, resolved bugs, deployments, important blockers, "
                "and concrete follow-ups. "
                "Do not propose raw transcript details, temporary status, repeated commands, "
                "low-value logs, stack traces, or vague summaries. "
                "Never include secrets, bearer tokens, passwords, private keys, cookie values, "
                "API keys, or raw credentials. "
                "If auth or secrets were involved, propose only a safe high-level operational fact. "
                "Use semantic write actions only: write, replace, or forget. Prefer write. "
                "Use replace only for clearly current state updates. Use forget only when the note "
                "clearly says a prior memory is obsolete. "
                "Use small memory kinds only: fact, preference, state, decision, note. "
                "Subjects should be short fuzzy handles like 'dory', 'openclaw', 'hermes', "
                "a project name, or 'active'. "
                "Split unrelated projects into separate actions. Do not emit actions when there "
                "is no grounded durable change."
            ),
            user_prompt=(
                f"Source note path: {distilled_path}\n\n"
                "Create a conservative set of Dory semantic memory proposal actions from this source note:\n\n"
                f"{summary}"
            ),
            schema_name="proposal_actions",
            schema=_PROPOSAL_SCHEMA,
        )
        actions_payload = payload.get("actions")
        if not isinstance(actions_payload, list):
            return []
        actions: list[ProposalAction] = []
        for raw_action in actions_payload:
            if not isinstance(raw_action, dict):
                continue
            action = str(raw_action.get("action", "")).strip()
            kind = str(raw_action.get("kind", "")).strip()
            subject = str(raw_action.get("subject", "")).strip()
            if (
                action not in {"write", "replace", "forget"}
                or kind not in {"fact", "preference", "state", "decision", "note"}
                or not subject
            ):
                continue
            scope = _optional_enum(raw_action.get("scope"), {"person", "project", "concept", "decision", "core"})
            confidence = _optional_enum(raw_action.get("confidence"), {"high", "medium", "low"})
            actions.append(
                ProposalAction(
                    action=action,  # type: ignore[arg-type]
                    kind=kind,  # type: ignore[arg-type]
                    subject=subject,
                    content=str(raw_action.get("content", "")),
                    scope=scope,  # type: ignore[arg-type]
                    confidence=confidence,  # type: ignore[arg-type]
                    reason=_optional_string(raw_action.get("reason")),
                    source=_optional_string(raw_action.get("source")),
                    soft=bool(raw_action.get("soft", False)),
                )
            )
        return actions


def load_proposal(path: Path) -> ProposalDocument:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProposalDocument(
        proposal_id=str(payload["proposal_id"]),
        source_distilled_path=str(payload["source_distilled_path"]),
        backend=str(payload["backend"]),
        actions=[
            ProposalAction(
                action=str(action["action"]),  # type: ignore[arg-type]
                kind=str(action["kind"]),  # type: ignore[arg-type]
                subject=str(action["subject"]),
                content=str(action.get("content", "")),
                scope=_optional_enum(
                    action.get("scope"),
                    {"person", "project", "concept", "decision", "core"},
                ),  # type: ignore[arg-type]
                confidence=_optional_enum(
                    action.get("confidence"),
                    {"high", "medium", "low"},
                ),  # type: ignore[arg-type]
                reason=action.get("reason"),
                source=_optional_string(action.get("source")),
                soft=bool(action.get("soft", False)),
                force_inbox=bool(action.get("force_inbox", False)),
                dry_run=action.get("dry_run") if isinstance(action.get("dry_run"), dict) else None,
                risk=action.get("risk") if isinstance(action.get("risk"), dict) else None,
            )
            for action in payload.get("actions", [])
        ],
        proposal_kind=str(payload.get("proposal_kind", "dream")),
        status=_proposal_status(payload.get("status")),
        created_at=_optional_string(payload.get("created_at")),
        agent=_optional_string(payload.get("agent")),
        session_id=_optional_string(payload.get("session_id")),
        origin_surface=_optional_string(payload.get("origin_surface")),
        reason=_optional_string(payload.get("reason")),
        source_paths=_optional_string_list(payload.get("source_paths")),
        applied_at=_optional_string(payload.get("applied_at")),
        rejected_at=_optional_string(payload.get("rejected_at")),
        rejected_reason=_optional_string(payload.get("rejected_reason")),
    )


def list_proposals(root: Path) -> list[str]:
    proposals_root = Path(root) / "inbox" / "proposed"
    if not proposals_root.exists():
        return []
    return sorted(path.stem for path in proposals_root.glob("*.json"))


class ProposalStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list(self, *, status: Literal["pending", "applied", "rejected"] = "pending") -> list[str]:
        directory = self._directory_for_status(status)
        if not directory.exists():
            return []
        return sorted(path.stem for path in directory.glob("*.json"))

    def load(
        self,
        proposal_id: str,
        *,
        status: Literal["pending", "applied", "rejected"] = "pending",
    ) -> ProposalDocument:
        return load_proposal(self.path(proposal_id, status=status))

    def path(self, proposal_id: str, *, status: Literal["pending", "applied", "rejected"] = "pending") -> Path:
        directory = self._directory_for_status(status)
        safe_id = _validated_existing_proposal_id(proposal_id)
        candidate = directory / safe_id
        if candidate.suffix != ".json":
            candidate = candidate.with_suffix(".json")
        if not candidate.exists():
            raise DoryValidationError(f"proposal not found: {proposal_id}")
        return candidate

    def write_pending(self, proposal: ProposalDocument) -> Path:
        target = self.root / "inbox" / "proposed" / f"{proposal.proposal_id}.json"
        for status in ("pending", "applied", "rejected"):
            existing = self._directory_for_status(status) / target.name
            if existing.exists():
                raise DoryValidationError(f"proposal already exists: {proposal.proposal_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, proposal_to_payload(proposal))
        return target

    def archive(
        self,
        proposal_path: Path,
        proposal: ProposalDocument,
        *,
        status: Literal["applied", "rejected"],
    ) -> Path:
        target = self._directory_for_status(status) / proposal_path.name
        if target.exists():
            raise DoryValidationError(f"archived proposal already exists: {proposal.proposal_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, proposal_to_payload(proposal))
        proposal_path.unlink()
        return target

    def _directory_for_status(self, status: Literal["pending", "applied", "rejected"]) -> Path:
        if status == "pending":
            return self.root / "inbox" / "proposed"
        if status == "applied":
            return self.root / "inbox" / "applied"
        return self.root / "inbox" / "rejected"


@dataclass(frozen=True, slots=True)
class ProposalApplyResult:
    proposal_id: str
    applied: tuple[str, ...]
    archived_path: str


def create_semantic_write_proposal(
    *,
    root: Path,
    engine: SemanticWriteEngine,
    req: MemoryProposalCreateReq,
) -> tuple[ProposalDocument, Path]:
    proposal_id = _proposal_id(req)
    dry_run_req = _memory_write_req_from_proposal_req(req, dry_run=True, allow_canonical=False)
    dry_run_resp = engine.write(dry_run_req)
    dry_run_payload = dry_run_resp.model_dump(mode="json")
    action = ProposalAction(
        action=req.action,
        kind=req.kind,
        subject=req.subject,
        content=req.content,
        scope=req.scope,
        confidence=req.confidence,
        reason=req.reason,
        source=req.source,
        soft=req.soft,
        force_inbox=req.force_inbox,
        dry_run=dry_run_payload,
        risk=_risk_from_dry_run(dry_run_payload),
    )
    proposal = ProposalDocument(
        proposal_id=proposal_id,
        source_distilled_path="",
        backend="semantic-write",
        actions=[action],
        proposal_kind="semantic-write",
        status="pending",
        created_at=_utc_now(),
        agent=req.agent,
        session_id=req.session_id,
        origin_surface=req.origin_surface,
        reason=req.reason,
        source_paths=req.source_paths,
    )
    store = ProposalStore(root)
    return proposal, store.write_pending(proposal)


def apply_proposal(
    *,
    root: Path,
    engine: SemanticWriteEngine,
    proposal_id: str,
    agent: str | None = None,
    session_id: str | None = None,
    origin_surface: str | None = None,
) -> ProposalApplyResult:
    store = ProposalStore(root)
    proposal_path = store.path(proposal_id)
    proposal = load_proposal(proposal_path)
    if proposal.status != "pending":
        raise DoryValidationError(f"proposal is not pending: {proposal.proposal_id}")
    applied_targets: list[str] = []
    for action in proposal.actions:
        _assert_proposal_action_still_matches(engine, action)
    for action in proposal.actions:
        response = engine.write(
            _memory_write_req_from_action(
                action,
                proposal=proposal,
                dry_run=False,
                allow_canonical=True,
                agent=agent,
                session_id=session_id,
                origin_surface=origin_surface,
            )
        )
        if response.result in {"rejected", "quarantined"}:
            raise DoryValidationError(response.message or f"proposal action failed for subject {action.subject}")
        applied_targets.append(response.target_path or response.subject_ref or action.subject)

    archived = store.archive(
        proposal_path,
        _replace_proposal_status(
            proposal,
            status="applied",
            applied_at=_utc_now(),
        ),
        status="applied",
    )
    return ProposalApplyResult(
        proposal_id=proposal.proposal_id,
        applied=tuple(applied_targets),
        archived_path=archived.relative_to(root).as_posix(),
    )


def reject_proposal(
    *,
    root: Path,
    proposal_id: str,
    reason: str | None = None,
) -> str:
    store = ProposalStore(root)
    proposal_path = store.path(proposal_id)
    proposal = load_proposal(proposal_path)
    archived = store.archive(
        proposal_path,
        _replace_proposal_status(
            proposal,
            status="rejected",
            rejected_at=_utc_now(),
            rejected_reason=reason,
        ),
        status="rejected",
    )
    return archived.relative_to(root).as_posix()


def proposal_to_payload(proposal: ProposalDocument) -> dict[str, Any]:
    payload = {
        "proposal_id": proposal.proposal_id,
        "proposal_kind": proposal.proposal_kind,
        "status": proposal.status,
        "created_at": proposal.created_at,
        "source_distilled_path": proposal.source_distilled_path,
        "backend": proposal.backend,
        "agent": proposal.agent,
        "session_id": proposal.session_id,
        "origin_surface": proposal.origin_surface,
        "reason": proposal.reason,
        "source_paths": proposal.source_paths or [],
        "applied_at": proposal.applied_at,
        "rejected_at": proposal.rejected_at,
        "rejected_reason": proposal.rejected_reason,
        "actions": [asdict(action) for action in proposal.actions],
    }
    return {key: value for key, value in payload.items() if value is not None}


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _optional_enum(value: object, allowed: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized not in allowed:
        return None
    return normalized


def _proposal_status(value: object) -> Literal["pending", "applied", "rejected"]:
    if value in {"pending", "applied", "rejected"}:
        return value  # type: ignore[return-value]
    return "pending"


def _proposal_id(req: MemoryProposalCreateReq) -> str:
    explicit = (req.proposal_id or "").strip()
    if explicit:
        return _new_proposal_id_segment(explicit)
    subject = slugify_path_segment(req.subject) or "memory"
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{subject}-{uuid4().hex[:8]}"


def _new_proposal_id_segment(value: str) -> str:
    return slugify_path_segment(value).replace("/", "-").strip("-") or "memory"


def _validated_existing_proposal_id(value: str) -> str:
    candidate = value.strip()
    if not candidate or "\\" in candidate or "/" in candidate or candidate in {".", ".."}:
        raise DoryValidationError(f"invalid proposal id: {value}")
    return candidate


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _memory_write_req_from_proposal_req(
    req: MemoryProposalCreateReq,
    *,
    dry_run: bool,
    allow_canonical: bool,
) -> MemoryWriteReq:
    return MemoryWriteReq(
        action=req.action,
        kind=req.kind,
        subject=req.subject,
        content=req.content,
        scope=req.scope,
        confidence=req.confidence,
        reason=req.reason,
        source=req.source,
        soft=req.soft,
        dry_run=dry_run,
        force_inbox=req.force_inbox,
        allow_canonical=allow_canonical,
        agent=req.agent,
        session_id=req.session_id,
        origin_surface=req.origin_surface,
    )


def _memory_write_req_from_action(
    action: ProposalAction,
    *,
    proposal: ProposalDocument,
    dry_run: bool,
    allow_canonical: bool,
    agent: str | None = None,
    session_id: str | None = None,
    origin_surface: str | None = None,
) -> MemoryWriteReq:
    return MemoryWriteReq(
        action=action.action,
        kind=action.kind,
        subject=action.subject,
        content=action.content,
        scope=action.scope,
        confidence=action.confidence,
        reason=action.reason or proposal.reason,
        source=action.source,
        soft=action.soft,
        dry_run=dry_run,
        force_inbox=action.force_inbox,
        allow_canonical=allow_canonical,
        agent=agent or proposal.agent,
        session_id=session_id or proposal.session_id,
        origin_surface=origin_surface or proposal.origin_surface,
    )


def _assert_proposal_action_still_matches(engine: SemanticWriteEngine, action: ProposalAction) -> None:
    if action.dry_run is None:
        return
    if action.force_inbox:
        return
    response = engine.write(
        _memory_write_req_from_action(
            action,
            proposal=ProposalDocument(
                proposal_id="stale-check",
                source_distilled_path="",
                backend="semantic-write",
                actions=[action],
            ),
            dry_run=True,
            allow_canonical=False,
        )
    )
    current = response.model_dump(mode="json")
    for field in ("target_path", "subject_ref"):
        if current.get(field) != action.dry_run.get(field):
            raise DoryValidationError(
                f"stale proposal action for {action.subject}: {field} changed "
                f"from {action.dry_run.get(field)!r} to {current.get(field)!r}"
            )
    current_preview = current.get("preview")
    stored_preview = action.dry_run.get("preview")
    if isinstance(current_preview, dict) and isinstance(stored_preview, dict):
        for field in ("target_subject_ref", "target_path"):
            if current_preview.get(field) != stored_preview.get(field):
                raise DoryValidationError(
                    f"stale proposal action for {action.subject}: preview.{field} changed "
                    f"from {stored_preview.get(field)!r} to {current_preview.get(field)!r}"
                )


def _risk_from_dry_run(payload: dict[str, Any]) -> dict[str, Any]:
    preview = payload.get("preview")
    canonical_target = bool(preview.get("canonical_target")) if isinstance(preview, dict) else False
    return {
        "canonical_target": canonical_target,
        "low_confidence": payload.get("confidence") == "low",
        "quarantined": bool(payload.get("quarantined")),
        "rejected": payload.get("result") == "rejected",
    }


def _replace_proposal_status(
    proposal: ProposalDocument,
    *,
    status: Literal["applied", "rejected"],
    applied_at: str | None = None,
    rejected_at: str | None = None,
    rejected_reason: str | None = None,
) -> ProposalDocument:
    return ProposalDocument(
        proposal_id=proposal.proposal_id,
        source_distilled_path=proposal.source_distilled_path,
        backend=proposal.backend,
        actions=proposal.actions,
        proposal_kind=proposal.proposal_kind,
        status=status,
        created_at=proposal.created_at,
        agent=proposal.agent,
        session_id=proposal.session_id,
        origin_surface=proposal.origin_surface,
        reason=proposal.reason,
        source_paths=proposal.source_paths,
        applied_at=applied_at or proposal.applied_at,
        rejected_at=rejected_at or proposal.rejected_at,
        rejected_reason=rejected_reason or proposal.rejected_reason,
    )
