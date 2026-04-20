from __future__ import annotations

from dory_core.migration_resolve import (
    build_contradiction_record,
    choose_winning_atom,
    precedence_rank,
    route_by_confidence,
)
from dory_core.migration_types import MemoryAtom


def test_precedence_rank_prefers_canonical_over_evidence() -> None:
    assert precedence_rank("decisions/hybrid-schema.md") < precedence_rank("logs/sessions/claude/a.md")
    assert precedence_rank("digests/weekly/2026-W12.md") < precedence_rank("digests/daily/2026-04-13.md")


def test_choose_winning_atom_prefers_higher_precedence_source() -> None:
    left = MemoryAtom(
        kind="project_update",
        subject_ref="project:rooster",
        payload={"summary": "session"},
        evidence_path="logs/sessions/claude/macbook/2026-04-12.md",
        time_ref="2026-04-12",
        confidence="high",
    )
    right = MemoryAtom(
        kind="project_update",
        subject_ref="project:rooster",
        payload={"summary": "weekly"},
        evidence_path="digests/weekly/2026-W12.md",
        time_ref="2026-03-29",
        confidence="medium",
    )

    assert choose_winning_atom(left, right) == right


def test_choose_winning_atom_breaks_ties_by_newer_time_then_confidence() -> None:
    left = MemoryAtom(
        kind="decision",
        subject_ref="decision:migration",
        payload={"summary": "older"},
        evidence_path="sources/web/a.md",
        time_ref="2026-04-01",
        confidence="medium",
    )
    right = MemoryAtom(
        kind="decision",
        subject_ref="decision:migration",
        payload={"summary": "newer"},
        evidence_path="sources/web/b.md",
        time_ref="2026-04-03",
        confidence="low",
    )

    assert choose_winning_atom(left, right) == right


def test_route_by_confidence_is_three_band() -> None:
    assert route_by_confidence("high", canonicality="canonical") == "direct"
    assert route_by_confidence("medium", canonicality="canonical") == "cautious"
    assert route_by_confidence("low", canonicality="canonical") == "quarantine"


def test_contradiction_record_reports_winner_path() -> None:
    left = MemoryAtom(
        kind="concept_claim",
        subject_ref="concept:openclaw",
        payload={"summary": "old"},
        evidence_path="logs/sessions/claude/a.md",
        time_ref="2026-04-01",
        confidence="medium",
    )
    right = MemoryAtom(
        kind="concept_claim",
        subject_ref="concept:openclaw",
        payload={"summary": "new"},
        evidence_path="decisions/openclaw.md",
        time_ref="2026-04-03",
        confidence="high",
    )

    record = build_contradiction_record(
        subject_ref="concept:openclaw",
        left=left,
        right=right,
        reason="canonical decision beats session evidence",
    )

    assert record.subject_ref == "concept:openclaw"
    assert record.left_path == "logs/sessions/claude/a.md"
    assert record.right_path == "decisions/openclaw.md"
    assert record.winner_path == "decisions/openclaw.md"
    assert record.to_dict()["reason"] == "canonical decision beats session evidence"
