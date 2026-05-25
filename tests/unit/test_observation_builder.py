from __future__ import annotations

from pathlib import Path

from dory_core.claim_store import ClaimStore
from dory_core.observation_builder import ObservationBuilder
from dory_core.observation_store import ObservationStore


class TestObservationBuilder:
    def test_rebuild_from_empty_claim_store(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        result = builder.rebuild_from_claims()
        assert result["observations_created"] == 0
        assert result["source_claims"] == 0
        assert obs_store.count_observations() == 0

    def test_rebuild_from_claims_creates_observations(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        # Add some claims
        claim_store.add_claim(
            entity_id="person:avery",
            kind="fact",
            statement="Avery prefers written updates.",
            evidence_path="sources/semantic/2026/04/14/avery-write.md",
        )
        claim_store.add_claim(
            entity_id="project:sample",
            kind="state",
            statement="Sample is the active focus.",
            evidence_path="logs/sessions/claude/2026-04-14.md",
        )

        result = builder.rebuild_from_claims()
        assert result["observations_created"] == 2
        assert result["source_claims"] == 2

        assert obs_store.count_observations() == 2

    def test_every_observation_has_evidence(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="person:bob",
            kind="fact",
            statement="Bob prefers async work.",
            evidence_path="logs/sessions/claude/2026-04-15.md",
        )

        builder.rebuild_from_claims()

        observations = obs_store.list_observations()
        assert len(observations) == 1
        obs = observations[0]
        evidence = obs_store.get_evidence(obs.observation_id)
        assert len(evidence) == 1
        assert evidence[0].claim_id is not None
        assert evidence[0].evidence_path == "logs/sessions/claude/2026-04-15.md"
        assert evidence[0].quote == "Bob prefers async work."

    def test_rebuild_is_idempotent(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="person:avery",
            kind="fact",
            statement="Avery prefers written updates.",
            evidence_path="sources/semantic/2026/04/14/avery-write.md",
        )

        # First rebuild
        r1 = builder.rebuild_from_claims()
        assert r1["observations_created"] == 1

        # Second rebuild (same data)
        r2 = builder.rebuild_from_claims()
        assert r2["observations_created"] == 1  # Still 1 claim
        assert obs_store.count_observations() == 1  # No duplicates

    def test_rebuild_after_claim_update(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="project:sample",
            kind="state",
            statement="Sample is parked.",
            evidence_path="sources/legacy/projects/sample-old.md",
        )
        builder.rebuild_from_claims()
        assert obs_store.count_observations() == 1

        # Replace claim
        claim_store.replace_current_claim(
            entity_id="project:sample",
            kind="state",
            statement="Sample is the active focus.",
            evidence_path="digests/daily/2026-04-13-digest.md",
            reason="newer state",
        )
        builder.rebuild_from_claims()
        assert obs_store.count_observations() == 1  # Only active claims
        obs = obs_store.list_observations()[0]
        assert "Sample is the active focus" in obs.content

    def test_observation_inherits_claim_confidence(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="concept:idea",
            kind="note",
            statement="A tentative idea.",
            evidence_path="sources/notes/idea.md",
            confidence="medium",
        )

        builder.rebuild_from_claims()
        obs = obs_store.list_observations()[0]
        assert obs.confidence == "medium"

    def test_observation_title_from_claim(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="person:avery",
            kind="fact",
            statement="Avery prefers written updates over async communication.",
            evidence_path="sources/test.md",
        )

        builder.rebuild_from_claims()
        obs = obs_store.list_observations()[0]
        assert obs.title.startswith("fact:")

    def test_build_observation_from_claim(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_id = claim_store.add_claim(
            entity_id="person:test",
            kind="fact",
            statement="Test statement.",
            evidence_path="sources/test.md",
        )

        obs_id = builder.build_observation_from_claim(claim_id)
        assert obs_id is not None
        obs = obs_store.get_observation(obs_id)
        assert obs is not None
        assert obs.content == "Test statement."
        evidence = obs_store.get_evidence(obs_id)
        assert len(evidence) == 1
        assert evidence[0].claim_id == claim_id

    def test_build_observation_from_nonexistent_claim(self, tmp_path: Path) -> None:
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        result = builder.build_observation_from_claim("nonexistent")
        assert result is None

    def test_freshness_deterministic(self, tmp_path: Path) -> None:
        """Freshness is deterministic, no LLM involved."""
        claim_store = ClaimStore(tmp_path / "claims.db")
        obs_store = ObservationStore(tmp_path / "observations.db")
        builder = ObservationBuilder(claim_store, obs_store)

        claim_store.add_claim(
            entity_id="person:test",
            kind="fact",
            statement="Recent fact.",
            evidence_path="sources/test.md",
        )

        builder.rebuild_from_claims()
        obs = obs_store.list_observations()[0]
        # Freshness should be one of the deterministic values
        assert obs.freshness in ("new", "stable", "stale")
