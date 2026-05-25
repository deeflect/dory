from __future__ import annotations

from pathlib import Path

from dory_core.observation_retrieval import ObservationRetrieval
from dory_core.observation_store import ObservationEvidence, ObservationStore


class TestObservationRetrieval:
    def test_get_observation_returns_none_when_db_absent(self, tmp_path: Path) -> None:
        """If the observation DB is absent, retrieval returns empty quickly."""
        store = ObservationStore(tmp_path / "nonexistent" / "obs.db")
        retrieval = ObservationRetrieval(store)
        assert retrieval.get_observation("anything") is None
        assert retrieval.find_by_entity("person:avery") == ()
        assert retrieval.find_active() == ()
        assert retrieval.count_active() == 0
        assert retrieval.count_all() == 0

    def test_get_observation_with_evidence(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)
        obs_id = store.add_observation(
            title="fact: test",
            content="Test content.",
            entity_ids=("person:avery",),
            evidence_rows=(
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-1",
                    evidence_path="path/to/evidence.md",
                    quote="Quote from evidence.",
                    relevance="high",
                    observed_at="2026-05-01T00:00:00Z",
                ),
            ),
        )
        obs, evidence = retrieval.get_observation_with_evidence(obs_id)
        assert obs is not None
        assert obs.observation_id == obs_id
        assert len(evidence) == 1
        assert evidence[0].claim_id == "claim-1"

    def test_get_observation_with_evidence_nonexistent(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)
        obs, evidence = retrieval.get_observation_with_evidence("nonexistent")
        assert obs is None
        assert evidence == ()

    def test_find_by_entity(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        store.add_observation(
            title="fact: Avery prefer...",
            content="Avery prefers written updates.",
            entity_ids=("person:avery",),
            evidence_rows=(
                ObservationEvidence("", "c1", "e1.md", "Q1", "high", "2026-05-01T00:00:00Z"),
            ),
        )
        store.add_observation(
            title="fact: Bob prefer...",
            content="Bob prefers async work.",
            entity_ids=("person:bob",),
            evidence_rows=(
                ObservationEvidence("", "c2", "e2.md", "Q2", "high", "2026-05-02T00:00:00Z"),
            ),
        )

        anna_obs = retrieval.find_by_entity("person:avery")
        assert len(anna_obs) == 1
        assert anna_obs[0].content == "Avery prefers written updates."

        bob_obs = retrieval.find_by_entity("person:bob")
        assert len(bob_obs) == 1
        assert bob_obs[0].content == "Bob prefers async work."

    def test_find_by_entity_with_status_filter(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        obs_id = store.add_observation(
            title="fact: retired fact",
            content="Retired content.",
            entity_ids=("person:avery",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        store.retire_observation(obs_id)

        # Should still find by entity (returns all statuses)
        obs = retrieval.find_by_entity("person:avery")
        assert len(obs) == 1

        # Filter by status
        obs = retrieval.find_by_entity("person:avery", status="retired")
        assert len(obs) == 1

        obs = retrieval.find_by_entity("person:avery", status="active")
        assert len(obs) == 0

    def test_find_active(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        store.add_observation(
            title="fact: active",
            content="Active.",
            entity_ids=("person:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        id2 = store.add_observation(
            title="fact: retired",
            content="Retired.",
            entity_ids=("project:b",),
            evidence_rows=(ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),),
        )
        store.retire_observation(id2)

        active = retrieval.find_active()
        assert len(active) == 1
        assert active[0].title == "fact: active"

    def test_find_recent(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        store.add_observation(
            title="state: older",
            content="Older.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        store.add_observation(
            title="state: newer",
            content="Newer.",
            entity_ids=("project:b",),
            evidence_rows=(ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),),
        )

        recent = retrieval.find_recent()
        # newer first due to ORDER BY updated_at DESC
        assert len(recent) == 2

    def test_find_stale(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        store.add_observation(
            title="fact: fresh",
            content="Fresh.",
            entity_ids=("person:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        id2 = store.add_observation(
            title="fact: stale",
            content="Stale.",
            entity_ids=("person:b",),
            freshness="stale",
            evidence_rows=(ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),),
        )

        stale = retrieval.find_stale()
        assert len(stale) == 1
        assert stale[0].observation_id == id2

    def test_find_by_entity_and_kind(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        store.add_observation(
            title="fact: test fact",
            content="A fact.",
            entity_ids=("person:avery",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        store.add_observation(
            title="state: project state",
            content="A state.",
            entity_ids=("person:avery",),
            evidence_rows=(ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),),
        )

        facts = retrieval.find_by_entity_and_kind("person:avery", "fact:")
        assert len(facts) == 1
        assert "fact:" in facts[0].title

    def test_get_evidence_for_observation(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        obs_id = store.add_observation(
            title="fact: test",
            content="Test.",
            entity_ids=("person:test",),
            evidence_rows=(
                ObservationEvidence("", "c1", "e1.md", "Q1", "high", "2026-05-01T00:00:00Z"),
                ObservationEvidence("", "c2", "e2.md", "Q2", "medium", None),
            ),
        )
        evidence = retrieval.get_evidence_for_observation(obs_id)
        assert len(evidence) == 2

    def test_count_methods(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        retrieval = ObservationRetrieval(store)

        assert retrieval.count_all() == 0
        assert retrieval.count_active() == 0

        store.add_observation(
            title="fact: test",
            content="Test.",
            entity_ids=("person:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        assert retrieval.count_all() == 1
        assert retrieval.count_active() == 1

    def test_missing_db_returns_empty_quickly(self, tmp_path: Path) -> None:
        """When the observation DB file doesn't exist, all retrievals return empty."""
        store = ObservationStore(tmp_path / "does-not-exist" / "obs.db")
        retrieval = ObservationRetrieval(store)

        assert retrieval.get_observation("x") is None
        assert retrieval.get_observation_with_evidence("x") == (None, ())
        assert retrieval.find_by_entity("e") == ()
        assert retrieval.find_active() == ()
        assert retrieval.find_recent() == ()
        assert retrieval.find_stale() == ()
        assert retrieval.get_evidence_for_observation("x") == ()
        assert retrieval.count_active() == 0
        assert retrieval.count_all() == 0
