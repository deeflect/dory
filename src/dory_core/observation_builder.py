from __future__ import annotations

from dory_core.claim_store import ClaimRecord, ClaimStore
from dory_core.observation_store import (
    ObservationEvidence,
    ObservationFreshness,
    ObservationStore,
)


class ObservationBuilder:
    """V1 Observation builder that derives observations from ClaimStore only.

    Every observation requires at least one evidence row derived from a
    claim's evidence_path. No unsupported synthesized insight rows are
    created. Freshness is deterministic (no LLM).

    This is a rebuildable indexer — run build_from_claim_store() to
    populate or refresh the observation store from the current set of
    claims.
    """

    def __init__(self, claim_store: ClaimStore, observation_store: ObservationStore) -> None:
        self._claim_store = claim_store
        self._observation_store = observation_store

    def rebuild_from_claims(self) -> dict[str, int]:
        """Rebuild the entire observation store from the claim store.

        This clears existing observations and re-derives them from active
        claims only (V1). Returns a summary dict with counts.

        Steps:
        1. Clear all existing observations.
        2. Iterate over recent active claims and create one observation
           per claim, with the claim's evidence as the source reference.
        3. Observations inherit deterministic freshness based on recency.
        """
        self._observation_store.clear_all()

        active_claims = self._claim_store.recent_active_claims(limit=10000)
        created = 0

        for claim in active_claims:
            self._add_observation_from_claim(claim)
            created += 1

        return {"observations_created": created, "source_claims": created}

    def build_observation_from_claim(self, claim_id: str) -> str | None:
        """Derive a single observation from an existing claim.

        Returns the observation_id, or None if the claim is not found.
        This is useful for incremental updates after a new claim is added.
        """
        # Fetch the specific claim — we need to work around the claim store's API
        # by scanning recent active claims
        for claim in self._claim_store.recent_active_claims(limit=10000):
            if claim.claim_id == claim_id:
                return self._add_observation_from_claim(claim)
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_observation_from_claim(self, claim: ClaimRecord) -> str:
        """Create an observation + evidence row from a single claim."""
        freshness = _determine_freshness(claim)
        evidence = ObservationEvidence(
            observation_id="",  # Will be filled by the store
            claim_id=claim.claim_id,
            evidence_path=claim.evidence_path,
            quote=claim.statement,
            relevance="high",
            observed_at=claim.valid_from or claim.created_at,
        )
        # Build a human-readable title from the claim
        title = _title_from_claim(claim)
        return self._observation_store.add_observation(
            title=title,
            content=claim.statement,
            entity_ids=(claim.entity_id,),
            status="active",
            freshness=freshness,
            confidence=claim.confidence,  # type: ignore[arg-type]
            evidence_rows=(evidence,),
        )


# ---------------------------------------------------------------------------
# Deterministic freshness helpers (no LLM)
# ---------------------------------------------------------------------------


def _determine_freshness(claim: ClaimRecord) -> ObservationFreshness:
    """Determine freshness based on claim's updated_at recency.

    Rules:
    - updated_at within last 7 days -> "new"
    - updated_at within last 30 days -> "stable"
    - older than 30 days -> "stale"
    """
    from datetime import UTC, datetime, timedelta

    try:
        updated = datetime.fromisoformat(claim.updated_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "stable"

    now = datetime.now(tz=UTC)
    delta = now - updated

    if delta < timedelta(days=7):
        return "new"
    elif delta < timedelta(days=30):
        return "stable"
    else:
        return "stale"


def _title_from_claim(claim: ClaimRecord) -> str:
    """Build a short title from a claim for the observation.

    V1 uses the kind + a short prefix of the statement.
    """
    max_prefix = 60
    statement_preview = claim.statement[:max_prefix]
    if len(claim.statement) > max_prefix:
        statement_preview += "..."
    return f"{claim.kind}: {statement_preview}"
