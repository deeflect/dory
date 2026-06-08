from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dory_core.active_memory_policy import SourcePolicy
from dory_core.entity_context import EntityContext
from dory_core.hot_context import SourceBackedItem
from dory_core.markdown_excerpt import truncate_text
from dory_core.observation_store import (
    ObservationEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
)


class ObservationRetrievalBackend(Protocol):
    def find_by_entity(
        self,
        entity_id: str,
        *,
        status: ObservationStatus | None = None,
        freshness: ObservationFreshness | None = None,
        limit: int = 20,
    ) -> tuple[ObservationRecord, ...]: ...

    def get_evidence_for_observation(self, observation_id: str) -> tuple[ObservationEvidence, ...]: ...


@dataclass(frozen=True, slots=True)
class ObservationAdmissionResult:
    items: tuple[SourceBackedItem, ...] = ()
    warnings: tuple[str, ...] = ()


def admitted_observation_items(
    *,
    observation_retrieval: ObservationRetrievalBackend | None,
    entity_context: EntityContext | None,
    source_policy: SourcePolicy,
    max_items: int = 2,
) -> tuple[SourceBackedItem, ...]:
    return admit_observations(
        observation_retrieval=observation_retrieval,
        entity_context=entity_context,
        source_policy=source_policy,
        max_items=max_items,
    ).items


def admit_observations(
    *,
    observation_retrieval: ObservationRetrievalBackend | None,
    entity_context: EntityContext | None,
    source_policy: SourcePolicy,
    max_items: int = 2,
) -> ObservationAdmissionResult:
    """Return source-backed observations admitted for active-memory rendering.

    Observations are derived context, so they fail closed: no resolved
    entity means no observation injection.  The observation itself must be
    active, not stale, medium/high confidence, and backed by evidence that
    the current profile is allowed to retrieve.
    """
    if observation_retrieval is None or entity_context is None:
        return ObservationAdmissionResult()
    if max_items <= 0 or not source_policy.retrieval.include_durable_context:
        return ObservationAdmissionResult()

    records = observation_retrieval.find_by_entity(
        entity_context.entity_id,
        status="active",
        limit=max_items * 4,
    )
    admitted: list[SourceBackedItem] = []
    withheld_for_quality = 0
    withheld_for_policy = 0
    for record in records:
        if not _is_admissible_record(record):
            withheld_for_quality += 1
            continue
        evidence = _first_admitted_evidence(
            observation_retrieval.get_evidence_for_observation(record.observation_id),
            source_policy=source_policy,
        )
        if evidence is None:
            withheld_for_policy += 1
            continue
        admitted.append(
            SourceBackedItem(
                text=truncate_text(_observation_text(record), 220),
                source_path=evidence.evidence_path,
            )
        )
        if len(admitted) >= max_items:
            break
    warnings = _warnings(withheld_for_quality=withheld_for_quality, withheld_for_policy=withheld_for_policy)
    return ObservationAdmissionResult(items=tuple(admitted), warnings=warnings)


def _is_admissible_record(record: ObservationRecord) -> bool:
    return (
        record.status == "active"
        and record.freshness != "stale"
        and record.confidence in {"medium", "high"}
        and bool(record.content.strip())
    )


def _first_admitted_evidence(
    evidence_rows: tuple[ObservationEvidence, ...],
    *,
    source_policy: SourcePolicy,
) -> ObservationEvidence | None:
    for evidence in evidence_rows:
        if evidence.relevance not in {"medium", "high"}:
            continue
        if source_policy.allows_result_path(evidence.evidence_path, corpus="durable"):
            return evidence
    return None


def _observation_text(record: ObservationRecord) -> str:
    title = record.title.strip()
    content = record.content.strip()
    if not title:
        return content
    if content.casefold().startswith(title.casefold()):
        return content
    return f"{title}: {content}"


def _warnings(*, withheld_for_quality: int, withheld_for_policy: int) -> tuple[str, ...]:
    warnings: list[str] = []
    if withheld_for_quality:
        warnings.append("Stale or low-confidence observations were withheld from active memory.")
    if withheld_for_policy:
        warnings.append("Some observations were withheld by the active profile source policy.")
    return tuple(warnings)
