from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from dory_core.llm.openrouter import OpenRouterClient
from dory_core.types import MemoryWriteAction, MemoryWriteKind


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


@dataclass(frozen=True, slots=True)
class ProposalDocument:
    proposal_id: str
    source_distilled_path: str
    backend: str
    actions: list[ProposalAction]


class ProposalGenerator:
    def __init__(
        self,
        root: Path,
        backend: str,
        *,
        client: OpenRouterClient | None = None,
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
        )
        payload = {
            "proposal_id": proposal.proposal_id,
            "source_distilled_path": proposal.source_distilled_path,
            "backend": proposal.backend,
            "actions": [asdict(action) for action in proposal.actions],
        }
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
                "You convert a distilled memory note into reviewable Dory semantic memory proposals. "
                "Use only facts present in the distilled note. "
                "Do not use markdown paths or target files. "
                "Use semantic write actions only: write, replace, or forget. "
                "Use small memory kinds only: fact, preference, state, decision, note. "
                "Prefer write. Use replace only for clearly current state updates. "
                "Use forget only when the note clearly says a prior memory is obsolete. "
                "Subjects should be short fuzzy human-readable handles like 'teammate', 'dory', 'rooster', or 'active'. "
                "Do not emit actions when there is no grounded change to propose."
            ),
            user_prompt=(
                f"Distilled note path: {distilled_path}\n\n"
                "Create a conservative set of Dory semantic memory proposal actions from this note:\n\n"
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
                scope=_optional_enum(action.get("scope"), {"person", "project", "concept", "decision", "core"}),  # type: ignore[arg-type]
                confidence=_optional_enum(action.get("confidence"), {"high", "medium", "low"}),  # type: ignore[arg-type]
                reason=action.get("reason"),
                source=_optional_string(action.get("source")),
                soft=bool(action.get("soft", False)),
            )
            for action in payload.get("actions", [])
        ],
    )


def list_proposals(root: Path) -> list[str]:
    proposals_root = Path(root) / "inbox" / "proposed"
    if not proposals_root.exists():
        return []
    return sorted(path.stem for path in proposals_root.glob("*.json"))


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_enum(value: object, allowed: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized not in allowed:
        return None
    return normalized
