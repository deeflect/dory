from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from dory_core.canonical_pages import (
    infer_aliases_from_subject,
    render_canonical_from_claims,
    render_retired_canonical_from_claims,
)
from dory_core.claim_store import ClaimStore
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import load_markdown_document
from dory_core.semantic_write_plan import SemanticWritePlan
from dory_core.types import WriteReq, WriteResp
from dory_core.write import WriteEngine


@dataclass(frozen=True, slots=True)
class SemanticCanonicalPublisher:
    root: Path
    writer: WriteEngine
    claim_store: ClaimStore

    def rewrite_from_claims(self, plan: SemanticWritePlan, *, requested_subject: str) -> WriteResp:
        claims = self.claim_store.current_claims(plan.target_subject_ref)
        history = self.claim_store.claim_history(plan.target_subject_ref)
        events = self.claim_store.claim_events(plan.target_subject_ref)
        update = render_canonical_from_claims(
            family=plan.family,
            title=plan.title,
            entity_id=plan.target_subject_ref,
            claims=claims,
            history=history,
            events=events,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
        )
        document = load_markdown_document(update.body)
        target = Path(plan.target_path)
        write_kind = "replace" if (self.root / target).exists() else "create"
        expected_hash = self._current_hash_for_target(target) if write_kind == "replace" else None
        return self.writer.write(
            WriteReq(
                kind=write_kind,
                target=plan.target_path,
                content=document.body,
                frontmatter=document.frontmatter,
                soft=False,
                reason="claim-derived canonical rewrite",
                expected_hash=expected_hash,
            )
        )

    def rewrite_tombstone_from_claims(self, plan: SemanticWritePlan, *, requested_subject: str) -> None:
        history = self.claim_store.claim_history(plan.target_subject_ref)
        events = self.claim_store.claim_events(plan.target_subject_ref)
        if not history:
            return

        tombstone_target = Path(plan.target_path).with_name(f"{Path(plan.target_path).stem}.tombstone.md")
        update = render_retired_canonical_from_claims(
            family=plan.family,
            title=plan.title,
            entity_id=plan.target_subject_ref,
            history=history,
            events=events,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
            retirement_reason=plan.reason,
        )
        document = load_markdown_document(update.body)
        write_kind = "replace" if (self.root / tombstone_target).exists() else "create"
        expected_hash = self._current_hash_for_target(tombstone_target) if write_kind == "replace" else None
        self.writer.write(
            WriteReq(
                kind=write_kind,
                target=tombstone_target.as_posix(),
                content=document.body,
                frontmatter=document.frontmatter,
                soft=False,
                reason="claim-derived tombstone rewrite",
                expected_hash=expected_hash,
            )
        )

    def _current_hash_for_target(self, target: Path) -> str:
        current = self.root / target
        if not current.exists():
            raise DoryValidationError(f"target does not exist: {target.as_posix()}")
        current_text = current.read_text(encoding="utf-8")
        return f"sha256:{sha256(current_text.encode('utf-8')).hexdigest()}"
