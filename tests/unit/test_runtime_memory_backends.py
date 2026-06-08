from __future__ import annotations

from pathlib import Path

from dory_core.claim_store import ClaimStore
from dory_core.observation_store import ObservationEvidence, ObservationStore
from dory_core.retrieval_planner import ClaimLookup, ObservationLookup, TypedRetrievalPlan
from dory_core.runtime import build_dory_runtime


def test_runtime_does_not_create_memory_sidecars_when_absent(tmp_path: Path, fake_embedder) -> None:
    runtime = build_dory_runtime(
        corpus_root=tmp_path,
        index_root=tmp_path / ".index",
        embedder=fake_embedder,
        query_expander=None,
        retrieval_planner=None,
        reranker=None,
    )

    assert runtime.retrieval.kernel_engine.claim_store is None
    assert runtime.retrieval.kernel_engine.observation_retrieval is None
    assert runtime.active_memory_engine.observation_retrieval is None
    assert not (tmp_path / ".dory" / "claim-store.db").exists()
    assert not (tmp_path / ".dory" / "observation-store.db").exists()


def test_runtime_wires_existing_claim_and_observation_backends(tmp_path: Path, fake_embedder) -> None:
    claim_store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    claim_store.add_claim(
        entity_id="project:dory",
        kind="state",
        statement="Dory runtime typed retrieval is wired.",
        evidence_path="projects/dory/state.md",
    )
    observation_store = ObservationStore(tmp_path / ".dory" / "observation-store.db")
    observation_store.add_observation(
        title="state: Dory runtime",
        content="Dory runtime observations are wired.",
        entity_ids=("project:dory",),
        evidence_rows=(ObservationEvidence("", "claim-1", "projects/dory/state.md", "quote", "high", None),),
    )

    runtime = build_dory_runtime(
        corpus_root=tmp_path,
        index_root=tmp_path / ".index",
        embedder=fake_embedder,
        query_expander=None,
        retrieval_planner=None,
        reranker=None,
    )
    results = runtime.retrieval.execute_typed_plan(
        TypedRetrievalPlan(
            attempts=(
                ClaimLookup(entity_id="project:dory"),
                ObservationLookup(entity_id="project:dory"),
            )
        )
    )

    assert runtime.retrieval.kernel_engine.claim_store is not None
    assert runtime.retrieval.kernel_engine.observation_retrieval is not None
    assert runtime.active_memory_engine.observation_retrieval is not None
    assert results[0].error is None
    assert results[1].error is None
    assert "Dory runtime typed retrieval is wired." in str(results[0].payload)
    assert "Dory runtime observations are wired." in str(results[1].payload)
