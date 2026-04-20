from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.types import MemoryWriteReq


def test_semantic_write_creates_evidence_artifact_and_claim_provenance(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "people").mkdir(parents=True)
    person_path = root / "people" / "avery.md"
    person_path.write_text(
        "---\ntitle: Avery\naliases:\n  - avery\n---\n# Avery\n",
        encoding="utf-8",
    )

    engine = SemanticWriteEngine(root)
    response = engine.write(
        MemoryWriteReq(
            action="write",
            kind="fact",
            subject="avery",
            content="Avery prefers written updates.",
            scope="person",
            source="cli",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.target_path == "people/avery.md"

    semantic_artifacts = sorted((root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 1

    artifact_path = semantic_artifacts[0].relative_to(root).as_posix()
    artifact = load_markdown_document(semantic_artifacts[0].read_text(encoding="utf-8"))
    assert artifact.frontmatter["type"] == "source"
    assert artifact.frontmatter["source_kind"] == "semantic"
    assert artifact.frontmatter["entity_id"] == "person:avery"
    assert artifact.frontmatter["action"] == "write"
    assert artifact.frontmatter["kind"] == "fact"
    assert artifact.frontmatter["origin_surface"] == "cli"
    assert artifact.frontmatter["canonical_target"] == "people/avery.md"
    assert "Avery prefers written updates." in artifact.body

    claims = engine.claim_store.current_claims("person:avery")
    assert len(claims) == 1
    assert claims[0].evidence_path == artifact_path

    events = engine.claim_store.claim_events("person:avery")
    assert len(events) == 1
    assert events[0].event_type == "added"
    assert events[0].evidence_path == artifact_path
