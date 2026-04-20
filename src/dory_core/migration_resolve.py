from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from dory_core.migration_types import MemoryAtom

ConfidenceRoute = Literal["direct", "cautious", "quarantine"]


@dataclass(frozen=True, slots=True)
class ContradictionRecord:
    subject_ref: str
    left_path: str
    right_path: str
    winner_path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_PRECEDENCE_PREFIXES: tuple[tuple[str, int], ...] = (
    ("decisions/", 0),
    ("people/", 1),
    ("projects/", 1),
    ("concepts/", 1),
    ("digests/weekly/", 2),
    ("digests/daily/", 3),
    ("logs/sessions/", 4),
    ("sources/imported/", 5),
    ("sources/legacy/", 5),
    ("sources/web/", 6),
    ("sources/research/", 6),
)


def precedence_rank(path: str) -> int:
    normalized = path.lstrip("/")
    for prefix, rank in _PRECEDENCE_PREFIXES:
        if normalized.startswith(prefix):
            return rank
    return 99


def choose_winning_atom(left: MemoryAtom, right: MemoryAtom) -> MemoryAtom:
    left_rank = precedence_rank(left.evidence_path)
    right_rank = precedence_rank(right.evidence_path)
    if left_rank != right_rank:
        return left if left_rank < right_rank else right

    left_time = left.time_ref or ""
    right_time = right.time_ref or ""
    if left_time != right_time:
        return left if left_time > right_time else right

    if left.confidence != right.confidence:
        return left if _confidence_rank(left.confidence) >= _confidence_rank(right.confidence) else right

    return left


def route_by_confidence(confidence: str, *, canonicality: str) -> ConfidenceRoute:
    normalized = confidence.strip().lower()
    if normalized == "high":
        return "direct"
    if normalized == "medium":
        return "cautious"
    return "quarantine"


def build_contradiction_record(
    *,
    subject_ref: str,
    left: MemoryAtom,
    right: MemoryAtom,
    reason: str,
) -> ContradictionRecord:
    winner = choose_winning_atom(left, right)
    return ContradictionRecord(
        subject_ref=subject_ref,
        left_path=left.evidence_path,
        right_path=right.evidence_path,
        winner_path=winner.evidence_path,
        reason=reason,
    )


def _confidence_rank(confidence: str) -> int:
    normalized = confidence.strip().lower()
    if normalized == "high":
        return 2
    if normalized == "medium":
        return 1
    return 0
