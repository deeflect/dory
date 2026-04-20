from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MigrationEventKind = Literal[
    "scan_started",
    "scan_completed",
    "plan_completed",
    "file_started",
    "file_classified",
    "file_extracted",
    "file_quarantined",
    "subject_synthesized",
    "run_completed",
    "run_failed",
]


@dataclass(frozen=True, slots=True)
class MigrationRunEvent:
    kind: MigrationEventKind
    phase: str
    processed_count: int
    total_count: int
    path: str | None = None
    message: str | None = None
    llm_classified_count: int = 0
    llm_extracted_count: int = 0
    fallback_classified_count: int = 0
    fallback_extracted_count: int = 0
    atom_count: int = 0
    canonical_created_count: int = 0
    written_count: int = 0
    quarantined_count: int = 0
    contradiction_count: int = 0
