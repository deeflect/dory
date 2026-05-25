from __future__ import annotations

from pathlib import Path

import pytest

from dory_core.observation_store import (
    ObservationEvidence,
    ObservationRecord,
    ObservationStore,
)


class TestObservationRecordDataclass:
    def test_frozen(self) -> None:
        rec = ObservationRecord(
            observation_id="obs-1",
            title="state: Sample is active.",
            content="Sample is the active focus.",
            entity_ids=("project:sample",),
            status="active",
            freshness="new",
            confidence="high",
            created_at="2026-05-24T12:00:00Z",
            updated_at="2026-05-24T12:00:00Z",
        )
        with pytest.raises(AttributeError):
            rec.observation_id = "other"  # type: ignore[misc]

    def test_slots(self) -> None:
        rec = ObservationRecord(
            observation_id="obs-2",
            title="fact: Avery prefers...",
            content="Avery prefers written updates.",
            entity_ids=("person:avery",),
            status="active",
            freshness="stable",
            confidence="medium",
            created_at="2026-05-20T10:00:00Z",
            updated_at="2026-05-22T10:00:00Z",
        )
        with pytest.raises((AttributeError, TypeError)):
            rec.new_attr = "nope"  # type: ignore[attr-defined]

    def test_empty_entity_ids(self) -> None:
        rec = ObservationRecord(
            observation_id="obs-3",
            title="note: test",
            content="Test observation with no entities.",
            entity_ids=(),
            status="active",
            freshness="new",
            confidence="low",
            created_at="2026-05-24T12:00:00Z",
            updated_at="2026-05-24T12:00:00Z",
        )
        assert rec.entity_ids == ()


class TestObservationEvidenceDataclass:
    def test_frozen(self) -> None:
        ev = ObservationEvidence(
            observation_id="obs-1",
            claim_id="claim-abc",
            evidence_path="logs/sessions/claude/2026-04-14.md",
            quote="Sample is the active focus.",
            relevance="high",
            observed_at="2026-05-24T12:00:00Z",
        )
        with pytest.raises(AttributeError):
            ev.observation_id = "other"  # type: ignore[misc]

    def test_slots(self) -> None:
        ev = ObservationEvidence(
            observation_id="obs-1",
            claim_id=None,
            evidence_path="sources/semantic/2026/04/14/avery-write.md",
            quote="Avery prefers written updates.",
            relevance="medium",
            observed_at=None,
        )
        with pytest.raises((AttributeError, TypeError)):
            ev.new_attr = "nope"  # type: ignore[attr-defined]

    def test_null_claim_id(self) -> None:
        ev = ObservationEvidence(
            observation_id="obs-2",
            claim_id=None,
            evidence_path="digests/daily/2026-05-01-digest.md",
            quote="Some observed fact.",
            relevance="low",
            observed_at=None,
        )
        assert ev.claim_id is None
        assert ev.observed_at is None


class TestObservationStore:
    def test_add_and_get_observation(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="fact: Avery prefers written updates.",
            content="Avery prefers written updates.",
            entity_ids=("person:avery",),
            evidence_rows=(
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-abc",
                    evidence_path="sources/semantic/2026/04/14/avery-write.md",
                    quote="Avery prefers written updates.",
                    relevance="high",
                    observed_at="2026-05-20T10:00:00Z",
                ),
            ),
        )
        stored = store.get_observation(obs_id)
        assert stored is not None
        assert stored.title == "fact: Avery prefers written updates."
        assert stored.content == "Avery prefers written updates."
        assert stored.entity_ids == ("person:avery",)
        assert stored.status == "active"
        assert stored.freshness == "new"
        assert stored.confidence == "high"

    def test_add_observation_requires_evidence(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        with pytest.raises(ValueError, match="at least one source reference"):
            store.add_observation(
                title="no-evidence",
                content="This should fail.",
                entity_ids=("person:test",),
                evidence_rows=(),
            )

    def test_add_observation_requires_evidence_path(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        with pytest.raises(ValueError, match="evidence row requires a source reference"):
            store.add_observation(
                title="missing-source-ref",
                content="This should fail.",
                entity_ids=("person:test",),
                evidence_rows=(
                    ObservationEvidence(
                        observation_id="",
                        claim_id="claim-1",
                        evidence_path="  ",
                        quote="Quote.",
                        relevance="high",
                        observed_at=None,
                    ),
                ),
            )

    def test_add_observation_stores_evidence(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="fact: test evidence",
            content="Test content.",
            entity_ids=("person:bob",),
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
        evidence = store.get_evidence(obs_id)
        assert len(evidence) == 1
        assert evidence[0].claim_id == "claim-1"
        assert evidence[0].evidence_path == "path/to/evidence.md"
        assert evidence[0].quote == "Quote from evidence."
        assert evidence[0].relevance == "high"

    def test_add_observation_with_multiple_evidence_rows(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="decision: focus change",
            content="Focus changed from A to B.",
            entity_ids=("project:sample", "person:avery"),
            evidence_rows=(
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-1",
                    evidence_path="path/to/evidence1.md",
                    quote="First source.",
                    relevance="high",
                    observed_at="2026-05-01T00:00:00Z",
                ),
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-2",
                    evidence_path="path/to/evidence2.md",
                    quote="Second source.",
                    relevance="medium",
                    observed_at="2026-05-02T00:00:00Z",
                ),
            ),
        )
        evidence = store.get_evidence(obs_id)
        assert len(evidence) == 2
        assert evidence[0].claim_id == "claim-1"
        assert evidence[1].claim_id == "claim-2"

    def test_update_observation(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="state: initial",
            content="Initial content.",
            entity_ids=("project:test",),
            evidence_rows=(
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-1",
                    evidence_path="path/to/evidence.md",
                    quote="Quote.",
                    relevance="high",
                    observed_at=None,
                ),
            ),
        )
        assert store.update_observation(obs_id, status="stale", freshness="stale")
        updated = store.get_observation(obs_id)
        assert updated is not None
        assert updated.status == "stale"
        assert updated.freshness == "stale"
        assert updated.title == "state: initial"  # unchanged

    def test_update_nonexistent_observation(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        assert not store.update_observation("nonexistent", title="new title")

    def test_retire_observation(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="state: test",
            content="Test content.",
            entity_ids=("project:test",),
            evidence_rows=(
                ObservationEvidence(
                    observation_id="",
                    claim_id="claim-1",
                    evidence_path="path/to/evidence.md",
                    quote="Quote.",
                    relevance="high",
                    observed_at=None,
                ),
            ),
        )
        assert store.retire_observation(obs_id)
        stored = store.get_observation(obs_id)
        assert stored is not None
        assert stored.status == "retired"

    def test_list_observations(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        id1 = store.add_observation(
            title="state: first",
            content="First content.",
            entity_ids=("project:alpha",),
            evidence_rows=(
                ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),
            ),
        )
        store.add_observation(
            title="state: second",
            content="Second content.",
            entity_ids=("project:beta",),
            evidence_rows=(
                ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),
            ),
        )

        all_obs = store.list_observations()
        assert len(all_obs) == 2

        # Filter by entity_id
        alpha_obs = store.list_observations(entity_id="project:alpha")
        assert len(alpha_obs) == 1
        assert alpha_obs[0].observation_id == id1

    def test_list_observations_with_status_filter(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        store.add_observation(
            title="state: active",
            content="Active.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        id2 = store.add_observation(
            title="state: retired",
            content="Retired.",
            entity_ids=("project:b",),
            evidence_rows=(ObservationEvidence("", "c2", "e2.md", "Q2", "high", None),),
        )
        store.retire_observation(id2)

        active = store.list_observations(status="active")
        assert len(active) == 1
        assert active[0].title == "state: active"

        retired = store.list_observations(status="retired")
        assert len(retired) == 1
        assert retired[0].title == "state: retired"

    def test_count_observations(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        assert store.count_observations() == 0
        assert store.count_observations(status="active") == 0

        store.add_observation(
            title="state: test",
            content="Test.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        assert store.count_observations() == 1
        assert store.count_observations(status="active") == 1

    def test_clear_all(self, tmp_path: Path) -> None:
        store = ObservationStore(tmp_path / "obs.db")
        store.add_observation(
            title="state: test",
            content="Test.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        assert store.count_observations() == 1
        store.clear_all()
        assert store.count_observations() == 0

    def test_drop_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "obs.db"
        store = ObservationStore(db_path)
        assert db_path.exists()
        store.drop_db()
        assert not db_path.exists()

    def test_rebuild_round_trip(self, tmp_path: Path) -> None:
        """Simulate a rebuild: clear_all then re-add."""
        store = ObservationStore(tmp_path / "obs.db")
        obs_id = store.add_observation(
            title="state: before rebuild",
            content="Before.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )

        # Rebuild
        store.clear_all()
        assert store.count_observations() == 0
        assert store.get_observation(obs_id) is None

        # Re-add
        store.add_observation(
            title="state: after rebuild",
            content="After.",
            entity_ids=("project:a",),
            evidence_rows=(ObservationEvidence("", "c1", "e1.md", "Q1", "high", None),),
        )
        assert store.count_observations() == 1
