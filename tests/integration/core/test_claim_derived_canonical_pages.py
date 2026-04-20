from __future__ import annotations

from dory_core.canonical_pages import render_canonical_from_claims, section_text
from dory_core.claim_store import ClaimStore


def test_render_canonical_page_from_current_claims_and_history(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    store.add_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers async work.",
        evidence_path="logs/sessions/a.md",
    )
    store.replace_current_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers written updates.",
        evidence_path="logs/sessions/b.md",
        reason="new signal",
    )

    update = render_canonical_from_claims(
        family="person",
        title="Anna",
        entity_id="person:anna",
        claims=store.current_claims("person:anna"),
        history=store.claim_history("person:anna"),
        events=store.claim_events("person:anna"),
        aliases=("anna",),
    )

    current_facts = section_text(update.body, "Current Facts")

    assert "## Current Facts" in update.body
    assert "Anna prefers written updates." in current_facts
    assert "Anna prefers async work." not in current_facts
    assert "logs/sessions/a.md" in update.body
    assert "logs/sessions/b.md" in update.body
    assert "## Timeline" in update.body
