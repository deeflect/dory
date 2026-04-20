from __future__ import annotations

from dory_core.claim_store import ClaimStore


def test_claim_store_invalidates_prior_claim_without_losing_history(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    claim_id = store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is active.",
        evidence_path="logs/sessions/claude/2026-04-14.md",
    )

    store.invalidate_claim(claim_id, reason="project paused")

    current = store.current_claims("project:rooster", kind="state")
    history = store.claim_history("project:rooster")
    events = store.claim_events("project:rooster")
    assert current == ()
    assert history[-1].status == "invalidated"
    assert history[-1].claim_id == claim_id
    assert events[-1].event_type == "invalidated"
    assert events[-1].evidence_path == "logs/sessions/claude/2026-04-14.md"


def test_claim_store_replaces_current_claim_and_preserves_history(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    first_id = store.add_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers async work.",
        evidence_path="logs/sessions/claude/2026-04-14.md",
    )

    second_id = store.replace_current_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers written updates.",
        evidence_path="logs/sessions/claude/2026-04-15.md",
        reason="new preference",
    )

    current = store.current_claims("person:anna", kind="fact")
    history = store.claim_history("person:anna")
    events = store.claim_events("person:anna")
    assert len(current) == 1
    assert current[0].claim_id == second_id
    assert current[0].statement == "Anna prefers written updates."
    assert any(item.claim_id == first_id and item.status == "replaced" for item in history)
    assert [event.event_type for event in events] == ["added", "replaced", "added"]
