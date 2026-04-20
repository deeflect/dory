from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Literal

Canonicality = Literal["canonical", "evidence", "artifact", "transient"]
Confidence = Literal["high", "medium", "low"]
SourceQuality = Literal["strong", "mixed", "weak"]
ResolutionMode = Literal["resolved", "evidence_only", "quarantine"]
MigrationAction = Literal[
    "route_final",
    "append_timeline",
    "merge_into_existing",
    "store_as_source",
    "store_as_reference",
    "quarantine",
]
TimeScope = Literal["current", "historical", "timeless", "mixed"]
Domain = Literal["personal", "work", "mixed"]


@dataclass(frozen=True, slots=True)
class ClassifiedDocument:
    doc_class: str
    canonicality: Canonicality
    target_path: str
    domain: Domain
    entity_refs: tuple[str, ...]
    decision_refs: tuple[str, ...]
    time_scope: TimeScope
    confidence: Confidence
    action: MigrationAction
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entity_refs"] = list(self.entity_refs)
        payload["decision_refs"] = list(self.decision_refs)
        return payload


@dataclass(frozen=True, slots=True)
class MemoryAtom:
    kind: str
    subject_ref: str
    payload: dict[str, Any]
    evidence_path: str
    time_ref: str | None
    confidence: Confidence

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MigrationEntityCandidate:
    ref: str
    display_name: str
    aliases: tuple[str, ...]
    confidence: Confidence

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aliases"] = list(self.aliases)
        return payload


@dataclass(frozen=True, slots=True)
class MigrationEntityMention:
    key: str
    ref: str
    family: str
    display_name: str
    aliases: tuple[str, ...]
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aliases"] = list(self.aliases)
        return payload


@dataclass(frozen=True, slots=True)
class MigrationEntityCluster:
    canonical_ref: str
    family: str
    display_name: str
    aliases: tuple[str, ...]
    member_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aliases"] = list(self.aliases)
        payload["member_keys"] = list(self.member_keys)
        return payload


@dataclass(frozen=True, slots=True)
class MigrationPageAudit:
    path: str
    verdict: Literal["pass", "review", "fail"]
    summary: str
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        return payload


@dataclass(frozen=True, slots=True)
class MigrationPageRepair:
    path: str
    apply: bool
    summary: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractedMigrationDocument:
    classified: ClassifiedDocument
    source_quality: SourceQuality
    resolution_mode: ResolutionMode
    quarantine_reason: str | None
    entity_candidates: tuple[MigrationEntityCandidate, ...]
    atoms: tuple[MemoryAtom, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "classified": self.classified.to_dict(),
            "source_quality": self.source_quality,
            "resolution_mode": self.resolution_mode,
            "quarantine_reason": self.quarantine_reason,
            "entity_candidates": [candidate.to_dict() for candidate in self.entity_candidates],
            "atoms": [atom.to_dict() for atom in self.atoms],
        }
