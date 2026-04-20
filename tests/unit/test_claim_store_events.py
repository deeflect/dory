from __future__ import annotations

from dory_core.claim_store import ClaimStore


def test_claim_store_records_event_evidence_path(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    claim_id = store.add_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers written updates.",
        evidence_path="sources/semantic/2026/04/14/anna-write.md",
    )

    events = store.claim_events("person:anna")
    assert len(events) == 1
    assert events[0].claim_id == claim_id
    assert events[0].entity_id == "person:anna"
    assert events[0].event_type == "added"
    assert events[0].evidence_path == "sources/semantic/2026/04/14/anna-write.md"


def test_claim_store_replace_records_replaced_and_added_events(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    store.add_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers async work.",
        evidence_path="sources/semantic/2026/04/14/anna-write.md",
    )
    store.replace_current_claim(
        entity_id="person:anna",
        kind="fact",
        statement="Anna prefers written updates.",
        evidence_path="sources/semantic/2026/04/15/anna-replace.md",
        reason="new signal",
    )

    events = store.claim_events("person:anna")
    assert [event.event_type for event in events] == ["added", "replaced", "added"]
    assert events[1].reason == "new signal"
    assert events[1].evidence_path == "sources/semantic/2026/04/15/anna-replace.md"
    assert events[2].evidence_path == "sources/semantic/2026/04/15/anna-replace.md"


def test_claim_store_replace_can_preserve_source_time_for_events(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster was parked.",
        evidence_path="sources/legacy/projects/rooster-old.md",
        occurred_at="2026-04-01T00:00:00Z",
    )
    store.replace_current_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is the active focus.",
        evidence_path="digests/daily/2026-04-13-digest.md",
        reason="newer state",
        occurred_at="2026-04-13T00:00:00Z",
    )

    current = store.current_claims("project:rooster", kind="state")
    history = store.claim_history("project:rooster")
    events = store.claim_events("project:rooster")

    assert len(current) == 1
    assert current[0].statement == "Rooster is the active focus."
    assert current[0].valid_from == "2026-04-13T00:00:00Z"
    assert history[0].status == "replaced"
    assert history[0].valid_to == "2026-04-13T00:00:00Z"
    assert [event.created_at for event in events] == [
        "2026-04-01T00:00:00Z",
        "2026-04-13T00:00:00Z",
        "2026-04-13T00:00:00Z",
    ]


def test_claim_store_add_claim_can_preserve_source_time_for_events(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    claim_id = store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster was parked.",
        evidence_path="sources/legacy/projects/rooster-old.md",
        occurred_at="2026-04-01T00:00:00Z",
    )

    history = store.claim_history("project:rooster")
    events = store.claim_events("project:rooster")

    assert history[0].claim_id == claim_id
    assert history[0].valid_from == "2026-04-01T00:00:00Z"
    assert events[0].created_at == "2026-04-01T00:00:00Z"
