"""Kernel retrieval engine — typed attempt executor.

Executes a ``TypedRetrievalPlan`` (a list of typed retrieval attempts)
through deterministic, budgeted calls to the appropriate backend
primitives: entity context resolution, claim store, observation
retrieval, session recall, durable search, and link/neighbors.

Design rules (from Slice 7):
- Strict schema validation is enforced at plan construction time.
- Execution remains deterministic and budgeted.
- Planner output never writes memory.
- On planner failure, fall back to existing deterministic behaviour.
- Do not run all strategies on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dory_core.entity_context import (
    resolve_entity_context,
)
from dory_core.retrieval_planner import (
    ClaimLookup,
    DurableSearch,
    EntityLookup,
    LinkNeighbors,
    ObservationLookup,
    RetrievalAttempt,
    SessionRecall,
    TypedRetrievalPlan,
)
from dory_core.types import SearchReq, SearchScope


@dataclass(frozen=True, slots=True)
class KernelAttemptResult:
    """The result of executing a single typed retrieval attempt.

    ``kind`` mirrors the attempt kind string (``entity_lookup``, etc.).
    ``payload`` is the domain-specific returned value:
    - entity_lookup → ``EntityContext | None``
    - claim_lookup → ``tuple[dict[str, object], ...]``
    - observation_lookup → ``tuple[dict[str, object], ...]``
    - session_recall → ``list[SearchResult]``
    - durable_search → ``list[SearchResult]``
    - link_neighbors → ``dict[str, object]``
    ``error`` is non-None when the attempt failed.
    """

    kind: str
    payload: object | None = None
    error: str | None = None


@dataclass(slots=True)
class KernelRetrievalEngine:
    """Deterministic, budgeted executor for typed retrieval plans.

    Requires the same backend primitives that *active-memory* and *search*
    already use: an entity context resolver, claim store, observation
    retrieval, session plane, durable search engine, and link service.

    All fields are **optional** — unsupported attempt kinds raise errors
    at execution time, not at construction time.
    """

    root: Path | None = None
    search_engine: object | None = None
    claim_store: object | None = None
    observation_retrieval: object | None = None
    link_service: object | None = None

    # Entity resolution helpers (from entity_context module)
    entity_resolution_project: str | None = None
    entity_resolution_cwd: str | None = None

    def execute(
        self,
        plan: TypedRetrievalPlan,
        *,
        max_attempts: int = 8,
        source_policy: object | None = None,
    ) -> list[KernelAttemptResult]:
        """Execute a typed retrieval plan with budgeted iteration.

        Returns one ``KernelAttemptResult`` per attempt in the plan,
        up to ``max_attempts``.  Additional attempts beyond the limit
        are silently dropped.
        """
        if plan.empty:
            return []
        results: list[KernelAttemptResult] = []
        for attempt in plan.attempts[:max_attempts]:
            result = self._execute_one(attempt, source_policy=source_policy)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Individual attempt dispatcher
    # ------------------------------------------------------------------

    def _execute_one(
        self,
        attempt: RetrievalAttempt,
        *,
        source_policy: object | None = None,
    ) -> KernelAttemptResult:
        try:
            if isinstance(attempt, EntityLookup):
                return self._entity_lookup(attempt)
            if isinstance(attempt, ClaimLookup):
                return self._claim_lookup(attempt)
            if isinstance(attempt, ObservationLookup):
                return self._observation_lookup(attempt)
            if isinstance(attempt, SessionRecall):
                return self._session_recall(attempt, source_policy=source_policy)
            if isinstance(attempt, DurableSearch):
                return self._durable_search(attempt, source_policy=source_policy)
            if isinstance(attempt, LinkNeighbors):
                return self._link_neighbors(attempt)
            return KernelAttemptResult(kind="unknown", error=f"Unknown attempt type: {type(attempt).__name__}")
        except Exception as exc:
            return KernelAttemptResult(
                kind=_attempt_kind_name(attempt),
                error=f"Execution failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Attempt kind handlers
    # ------------------------------------------------------------------

    def _entity_lookup(self, attempt: EntityLookup) -> KernelAttemptResult:
        ctx = resolve_entity_context(
            attempt.name,
            family=attempt.family,
            root=self.root,
            project=self.entity_resolution_project,
            cwd=self.entity_resolution_cwd,
        )
        return KernelAttemptResult(kind="entity_lookup", payload=ctx)

    def _claim_lookup(self, attempt: ClaimLookup) -> KernelAttemptResult:
        if self.claim_store is None:
            return KernelAttemptResult(kind="claim_lookup", error="claim_store not configured")
        store = self.claim_store
        try:
            claims = store.current_claims(attempt.entity_id, kind=attempt.kind)
        except Exception as exc:
            return KernelAttemptResult(kind="claim_lookup", error=f"claim lookup failed: {exc}")
        payload = tuple(
            {
                "claim_id": c.claim_id,
                "entity_id": c.entity_id,
                "kind": c.kind,
                "statement": c.statement,
                "status": c.status,
                "confidence": c.confidence,
                "evidence_path": c.evidence_path,
            }
            for c in claims
        )
        return KernelAttemptResult(kind="claim_lookup", payload=payload)

    def _observation_lookup(self, attempt: ObservationLookup) -> KernelAttemptResult:
        if self.observation_retrieval is None:
            return KernelAttemptResult(kind="observation_lookup", error="observation_retrieval not configured")
        retrieval = self.observation_retrieval
        try:
            if attempt.entity_id:
                obs = retrieval.find_by_entity(attempt.entity_id, limit=20)
            else:
                obs = retrieval.find_active(limit=20)
        except Exception as exc:
            return KernelAttemptResult(kind="observation_lookup", error=f"observation lookup failed: {exc}")
        payload = tuple(
            {
                "observation_id": o.observation_id,
                "title": o.title,
                "content": o.content,
                "entity_ids": o.entity_ids,
                "status": o.status,
                "freshness": o.freshness,
                "confidence": o.confidence,
                "created_at": o.created_at,
                "updated_at": o.updated_at,
            }
            for o in obs
        )
        return KernelAttemptResult(kind="observation_lookup", payload=payload)

    def _session_recall(
        self,
        attempt: SessionRecall,
        *,
        source_policy: object | None = None,
    ) -> KernelAttemptResult:
        if self.search_engine is None:
            return KernelAttemptResult(kind="session_recall", error="search_engine not configured")
        scope = SearchScope()
        if attempt.scope:
            scope = SearchScope(agent=[attempt.scope]) if attempt.scope else scope
        try:
            response = self.search_engine.search(
                SearchReq(
                    query=attempt.query,
                    mode="recall",
                    corpus="sessions",
                    k=5,
                    include_content=False,
                    scope=scope,
                )
            )
        except Exception as exc:
            return KernelAttemptResult(kind="session_recall", error=f"session recall failed: {exc}")
        return KernelAttemptResult(
            kind="session_recall",
            payload=list(getattr(response, "results", [])),
        )

    def _durable_search(
        self,
        attempt: DurableSearch,
        *,
        source_policy: object | None = None,
    ) -> KernelAttemptResult:
        if self.search_engine is None:
            return KernelAttemptResult(kind="durable_search", error="search_engine not configured")
        try:
            response = self.search_engine.search(
                SearchReq(
                    query=attempt.query,
                    mode=attempt.mode,  # type: ignore[arg-type]
                    corpus="durable",
                    k=6,
                    include_content=True,
                )
            )
        except Exception as exc:
            return KernelAttemptResult(kind="durable_search", error=f"durable search failed: {exc}")
        return KernelAttemptResult(
            kind="durable_search",
            payload=list(getattr(response, "results", [])),
        )

    def _link_neighbors(self, attempt: LinkNeighbors) -> KernelAttemptResult:
        if self.link_service is None:
            return KernelAttemptResult(kind="link_neighbors", error="link_service not configured")
        try:
            result = self.link_service.neighbors(
                attempt.path,
                direction=attempt.direction,
                max_edges=20,
            )
        except Exception as exc:
            return KernelAttemptResult(kind="link_neighbors", error=f"link neighbors failed: {exc}")
        return KernelAttemptResult(kind="link_neighbors", payload=result)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _attempt_kind_name(attempt: RetrievalAttempt) -> str:
    mapping = {
        EntityLookup: "entity_lookup",
        ClaimLookup: "claim_lookup",
        ObservationLookup: "observation_lookup",
        SessionRecall: "session_recall",
        DurableSearch: "durable_search",
        LinkNeighbors: "link_neighbors",
    }
    return mapping.get(type(attempt), "unknown")


# ------------------------------------------------------------------
# Convenience: simplify a kernel plan into flat results for the
# active-memory / search caller that just wants evidence lists.
# ------------------------------------------------------------------


def flatten_kernel_results(
    results: Sequence[KernelAttemptResult],
    *,
    source_policy: object | None = None,
) -> list[object]:
    """Flatten kernel attempt results into a single list of search-like items.

    Extracts ``SearchResult`` objects from ``durable_search`` and
    ``session_recall`` results.  Other attempt kind payloads are
    skipped — the caller inspects ``KernelAttemptResult`` directly
    for those.
    """
    flat: list[object] = []
    for result in results:
        if result.error:
            continue
        if result.kind in ("durable_search", "session_recall"):
            payload = result.payload
            if isinstance(payload, list):
                flat.extend(payload)
        # entity_lookup, claim_lookup, observation_lookup, link_neighbors
        # are left for the caller to inspect individually.
    return flat
