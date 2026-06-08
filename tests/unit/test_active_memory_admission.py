from __future__ import annotations

from pathlib import Path

from dory_core.active_memory_admission import admit_observations, admitted_observation_items
from dory_core.active_memory_policy import SourcePolicy
from dory_core.entity_context import EntityContext
from dory_core.observation_retrieval import ObservationRetrieval
from dory_core.observation_store import ObservationEvidence, ObservationStore
from dory_core.profiles import ProfileRegistry


def _source_policy(tmp_path: Path, profile: str = "coding") -> SourcePolicy:
    return SourcePolicy(
        profile=profile,
        retrieval=ProfileRegistry(tmp_path).retrieval_profile(profile),
        include_session_context=False,
    )


def _entity_context() -> EntityContext:
    return EntityContext(
        entity_id="project:dory",
        canonical_name="Dory",
        family="project",
        canonical_path="projects/dory/state.md",
        matched_by="project_handle",
        source_refs=("projects/dory/state.md",),
    )


def test_admits_active_source_backed_observation_for_resolved_entity(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.db")
    obs_id = store.add_observation(
        title="state: Dory",
        content="Dory needs low-bloat project-scoped memory.",
        entity_ids=("project:dory",),
        evidence_rows=(
            ObservationEvidence("", "claim-1", "projects/dory/state.md", "Dory needs low-bloat memory.", "high", None),
        ),
    )
    retrieval = ObservationRetrieval(store)

    items = admitted_observation_items(
        observation_retrieval=retrieval,
        entity_context=_entity_context(),
        source_policy=_source_policy(tmp_path),
    )

    assert len(items) == 1
    assert items[0].source_path == "projects/dory/state.md"
    assert "low-bloat project-scoped memory" in items[0].text
    assert obs_id


def test_rejects_observations_without_resolved_entity(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.db")
    store.add_observation(
        title="state: Dory",
        content="Dory needs low-bloat project-scoped memory.",
        entity_ids=("project:dory",),
        evidence_rows=(ObservationEvidence("", "claim-1", "projects/dory/state.md", "quote", "high", None),),
    )

    items = admitted_observation_items(
        observation_retrieval=ObservationRetrieval(store),
        entity_context=None,
        source_policy=_source_policy(tmp_path),
    )

    assert items == ()


def test_rejects_low_confidence_stale_and_denied_evidence(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.db")
    store.add_observation(
        title="state: low confidence",
        content="Low confidence should not enter active memory.",
        entity_ids=("project:dory",),
        confidence="low",
        evidence_rows=(ObservationEvidence("", "claim-1", "projects/dory/state.md", "quote", "high", None),),
    )
    store.add_observation(
        title="state: stale",
        content="Stale observations need review first.",
        entity_ids=("project:dory",),
        freshness="stale",
        evidence_rows=(ObservationEvidence("", "claim-2", "projects/dory/state.md", "quote", "high", None),),
    )
    store.add_observation(
        title="personal: denied",
        content="Coding profile must not admit personal evidence.",
        entity_ids=("project:dory",),
        evidence_rows=(ObservationEvidence("", "claim-3", "knowledge/personal/writing-voice.md", "quote", "high", None),),
    )

    items = admitted_observation_items(
        observation_retrieval=ObservationRetrieval(store),
        entity_context=_entity_context(),
        source_policy=_source_policy(tmp_path),
    )

    assert items == ()


def test_reports_withheld_observation_warnings(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.db")
    store.add_observation(
        title="state: stale",
        content="Stale observations need review first.",
        entity_ids=("project:dory",),
        freshness="stale",
        evidence_rows=(ObservationEvidence("", "claim-1", "projects/dory/state.md", "quote", "high", None),),
    )
    store.add_observation(
        title="personal: denied",
        content="Coding profile must not admit personal evidence.",
        entity_ids=("project:dory",),
        evidence_rows=(ObservationEvidence("", "claim-2", "knowledge/personal/writing-voice.md", "quote", "high", None),),
    )

    result = admit_observations(
        observation_retrieval=ObservationRetrieval(store),
        entity_context=_entity_context(),
        source_policy=_source_policy(tmp_path),
    )

    assert result.items == ()
    assert result.warnings == (
        "Stale or low-confidence observations were withheld from active memory.",
        "Some observations were withheld by the active profile source policy.",
    )
