from __future__ import annotations

from dory_core.observation_store import (
    ObservationEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    ObservationStore,
)


class ObservationRetrieval:
    """Retrieval layer over the observation store.

    Returns empty results quickly when the observation DB is absent or
    empty. All retrieval is deterministic and source-backed.
    """

    def __init__(self, store: ObservationStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Retrieval methods
    # ------------------------------------------------------------------

    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        """Get a single observation by ID."""
        if not self._store.db_path.exists():
            return None
        return self._store.get_observation(observation_id)

    def get_observation_with_evidence(
        self,
        observation_id: str,
    ) -> tuple[ObservationRecord | None, tuple[ObservationEvidence, ...]]:
        """Get an observation and its evidence rows together."""
        if not self._store.db_path.exists():
            return None, ()
        obs = self._store.get_observation(observation_id)
        if obs is None:
            return None, ()
        evidence = self._store.get_evidence(observation_id)
        return obs, evidence

    def find_by_entity(
        self,
        entity_id: str,
        *,
        status: ObservationStatus | None = None,
        freshness: ObservationFreshness | None = None,
        limit: int = 20,
    ) -> tuple[ObservationRecord, ...]:
        """Find observations for a given entity."""
        if not self._store.db_path.exists():
            return ()
        return self._store.list_observations(
            entity_id=entity_id,
            status=status,
            freshness=freshness,
            limit=limit,
        )

    def find_active(self, *, limit: int = 20) -> tuple[ObservationRecord, ...]:
        """Find all active observations, newest first."""
        if not self._store.db_path.exists():
            return ()
        return self._store.list_observations(status="active", limit=limit)

    def find_recent(self, *, limit: int = 20) -> tuple[ObservationRecord, ...]:
        """Find the most recent observations regardless of status."""
        if not self._store.db_path.exists():
            return ()
        return self._store.list_observations(limit=limit)

    def find_stale(self, *, limit: int = 20) -> tuple[ObservationRecord, ...]:
        """Find observations that are stale (need review)."""
        if not self._store.db_path.exists():
            return ()
        return self._store.list_observations(freshness="stale", limit=limit)

    def find_by_entity_and_kind(
        self,
        entity_id: str,
        kind_prefix: str,
        *,
        limit: int = 10,
    ) -> tuple[ObservationRecord, ...]:
        """Find observations for an entity whose title starts with kind_prefix.

        This is a simple client-side filter over the entity-matched results.
        """
        results = self.find_by_entity(entity_id, limit=limit * 2)
        filtered = [o for o in results if o.title.startswith(kind_prefix)]
        return tuple(filtered[:limit])

    def get_evidence_for_observation(
        self,
        observation_id: str,
    ) -> tuple[ObservationEvidence, ...]:
        """Get evidence rows for an observation."""
        if not self._store.db_path.exists():
            return ()
        return self._store.get_evidence(observation_id)

    def count_active(self) -> int:
        """Count active observations."""
        if not self._store.db_path.exists():
            return 0
        return self._store.count_observations(status="active")

    def count_all(self) -> int:
        """Count all observations."""
        if not self._store.db_path.exists():
            return 0
        return self._store.count_observations()
