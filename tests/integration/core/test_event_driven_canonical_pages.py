from __future__ import annotations

from dory_core.canonical_pages import render_canonical_from_claims, section_text
from dory_core.claim_store import ClaimEvent, ClaimRecord


def test_render_canonical_page_uses_events_for_timeline_and_evidence() -> None:
    history = (
        ClaimRecord(
            claim_id="claim-old",
            entity_id="person:anna",
            kind="fact",
            statement="Anna prefers async work.",
            status="replaced",
            valid_from="2026-04-01T09:00:00Z",
            valid_to="2026-04-02T09:00:00Z",
            confidence="high",
            evidence_path="logs/sessions/a.md",
            created_at="2026-04-01T09:00:00Z",
            updated_at="2026-04-02T09:00:00Z",
        ),
        ClaimRecord(
            claim_id="claim-current",
            entity_id="person:anna",
            kind="fact",
            statement="Anna prefers written updates.",
            status="active",
            valid_from="2026-04-03T09:00:00Z",
            valid_to=None,
            confidence="high",
            evidence_path="logs/sessions/b.md",
            created_at="2026-04-03T09:00:00Z",
            updated_at="2026-04-03T09:00:00Z",
        ),
    )
    events = (
        ClaimEvent(
            event_id="event-added-old",
            claim_id="claim-old",
            entity_id="person:anna",
            event_type="added",
            reason=None,
            evidence_path="sources/semantic/2026/04/14/anna-write.md",
            created_at="2026-04-14T12:00:00Z",
        ),
        ClaimEvent(
            event_id="event-replaced-old",
            claim_id="claim-old",
            entity_id="person:anna",
            event_type="replaced",
            reason="new signal",
            evidence_path="sources/semantic/2026/04/15/anna-replace.md",
            created_at="2026-04-15T12:00:00Z",
        ),
        ClaimEvent(
            event_id="event-added-current",
            claim_id="claim-current",
            entity_id="person:anna",
            event_type="added",
            reason="new signal",
            evidence_path="sources/semantic/2026/04/15/anna-replace.md",
            created_at="2026-04-15T12:00:01Z",
        ),
    )

    update = render_canonical_from_claims(
        family="person",
        title="Anna",
        entity_id="person:anna",
        claims=(history[-1],),
        history=history,
        events=events,
        aliases=("anna",),
    )

    timeline = section_text(update.body, "Timeline")
    evidence = section_text(update.body, "Evidence")

    assert "2026-04-14: Anna prefers async work." in timeline
    assert "2026-04-15: Replaced: Anna prefers async work." in timeline
    assert "2026-04-15: Anna prefers written updates." in timeline
    assert "sources/semantic/2026/04/14/anna-write.md" in evidence
    assert "sources/semantic/2026/04/15/anna-replace.md" in evidence
    assert "logs/sessions/a.md" not in evidence
    assert "logs/sessions/b.md" not in evidence
