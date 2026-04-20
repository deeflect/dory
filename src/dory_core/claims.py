from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    path: str
    line: str
    surface: str
    note: str


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    statement: str
    status: str
    confidence: str
    freshness: str
    sources: tuple[EvidenceRef, ...]
    last_reviewed: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["sources"] = [asdict(source) for source in self.sources]
        return payload
