from __future__ import annotations

from dory_core.migration_types import ClassifiedDocument, MemoryAtom


def test_classified_document_shape() -> None:
    result = ClassifiedDocument(
        doc_class="project_state",
        canonicality="canonical",
        target_path="projects/sample/state.md",
        domain="work",
        entity_refs=("project:sample",),
        decision_refs=(),
        time_scope="current",
        confidence="high",
        action="route_final",
        reason="direct project page",
    )

    assert result.doc_class == "project_state"
    assert result.target_path == "projects/sample/state.md"
    assert result.to_dict()["entity_refs"] == ["project:sample"]


def test_memory_atom_shape() -> None:
    atom = MemoryAtom(
        kind="project_update",
        subject_ref="project:sample",
        payload={"summary": "registry first"},
        evidence_path="digests/daily/2026-04-13.md",
        time_ref="2026-04-13",
        confidence="high",
    )

    assert atom.kind == "project_update"
    assert atom.subject_ref == "project:sample"
    assert atom.to_dict()["payload"] == {"summary": "registry first"}
