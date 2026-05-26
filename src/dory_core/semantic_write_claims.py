from __future__ import annotations

from dataclasses import dataclass

from dory_core.canonical_pages import canonical_title_from_subject, infer_aliases_from_subject
from dory_core.claim_store import ClaimStore
from dory_core.entity_registry import EntityRegistry
from dory_core.semantic_write_plan import SemanticWritePlan, family_from_subject_ref
from dory_core.slug import canonical_target_for_subject


@dataclass(frozen=True, slots=True)
class SemanticClaimRecorder:
    registry: EntityRegistry
    claim_store: ClaimStore

    def sync_registry(self, plan: SemanticWritePlan, *, requested_subject: str) -> None:
        self.registry.upsert(
            entity_id=plan.target_subject_ref,
            family=plan.family,
            title=plan.title,
            target_path=plan.target_path,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
        )
        if plan.subject_ref != plan.target_subject_ref:
            subject_family = family_from_subject_ref(plan.subject_ref)
            self.registry.upsert(
                entity_id=plan.subject_ref,
                family=subject_family,
                title=canonical_title_from_subject(plan.subject_ref),
                target_path=canonical_target_for_subject(plan.subject_ref),
                aliases=infer_aliases_from_subject(plan.subject_ref, requested_subject=requested_subject),
            )

    def record(self, plan: SemanticWritePlan, *, evidence_path: str) -> None:
        statement = plan.content.strip()
        if not statement:
            return

        confidence = plan.confidence or plan.match_confidence
        if plan.resolved_mode == "replace":
            if self._active_claim_exists(plan, statement=statement, evidence_path=evidence_path):
                return
            self.claim_store.replace_current_claim(
                entity_id=plan.target_subject_ref,
                kind=plan.kind,
                statement=statement,
                evidence_path=evidence_path,
                confidence=confidence,
                reason=plan.reason,
            )
            return

        if plan.resolved_mode == "forget":
            self.claim_store.retire_entity_claims(
                entity_id=plan.target_subject_ref,
                reason=plan.reason or f"semantic {plan.action}",
                kind=None if plan.kind == "note" else plan.kind,
                evidence_path=evidence_path,
            )
            return

        if self._active_claim_exists(plan, statement=statement, evidence_path=evidence_path):
            return
        self.claim_store.add_claim(
            entity_id=plan.target_subject_ref,
            kind=plan.kind,
            statement=statement,
            evidence_path=evidence_path,
            confidence=confidence,
        )

    def _active_claim_exists(self, plan: SemanticWritePlan, *, statement: str, evidence_path: str) -> bool:
        expected_statement = _normalize_claim_text(statement)
        return any(
            claim.evidence_path == evidence_path
            and _normalize_claim_text(claim.statement) == expected_statement
            for claim in self.claim_store.current_claims(plan.target_subject_ref, kind=plan.kind)
        )


def _normalize_claim_text(value: str) -> str:
    return " ".join(value.split())
