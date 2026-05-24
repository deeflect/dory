from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.types import MemoryWriteReq


def test_semantic_forget_removes_exact_canonical_bullet_without_retiring_entity(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "projects" / "dory").mkdir(parents=True)
    project_path = root / "projects" / "dory" / "state.md"
    duplicate_note = "On 2026-05-23, commit 83694fd was pushed to public Dory main."
    project_path.write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\ncanonical: true\naliases:\n  - dory\n---\n"
        "\n## Current State\n"
        "- Keep this detailed deployment note.\n"
        f"- {duplicate_note}\n"
        "\n## Open Work\n"
        "- Keep this open item.\n",
        encoding="utf-8",
    )

    engine = SemanticWriteEngine(root)
    response = engine.write(
        MemoryWriteReq(
            action="forget",
            kind="note",
            subject="dory",
            content=duplicate_note,
            scope="project",
            reason="duplicate deployment note",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.result == "forgotten"
    assert response.target_path == "projects/dory/state.md"
    assert response.evidence_path is not None
    updated = load_markdown_document(project_path.read_text(encoding="utf-8"))
    assert duplicate_note not in updated.body
    assert "Keep this detailed deployment note." in updated.body
    assert "Keep this open item." in updated.body
    assert updated.frontmatter["status"] == "active"
    assert updated.frontmatter["canonical"] is True
    assert not project_path.with_name("state.tombstone.md").exists()


def test_semantic_write_replay_reuses_existing_evidence_when_canonical_already_contains_content(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "projects" / "dory").mkdir(parents=True)
    project_path = root / "projects" / "dory" / "state.md"
    note = "Dory retries should not duplicate semantic evidence."
    project_path.write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\ncanonical: true\naliases:\n  - dory\n---\n"
        f"\n## Current State\n- {note}\n",
        encoding="utf-8",
    )
    existing_evidence = root / "sources" / "semantic" / "2026" / "05" / "23" / "dory-existing-write.md"
    existing_evidence.parent.mkdir(parents=True)
    existing_evidence.write_text(
        "---\ntitle: Semantic write for Dory\ntype: source\nstatus: done\ncanonical: false\n"
        "source_kind: semantic\nentity_id: project:dory\nsubject: dory\naction: write\nkind: state\n"
        "canonical_target: projects/dory/state.md\n---\n"
        f"\n{note}\n",
        encoding="utf-8",
    )

    engine = SemanticWriteEngine(root)
    response = engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="dory",
            content=note,
            scope="project",
            reason="retry after client timeout",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.result == "written"
    assert response.evidence_path == "sources/semantic/2026/05/23/dory-existing-write.md"
    assert response.message == "idempotent semantic write replay; existing evidence reused"
    assert project_path.read_text(encoding="utf-8").count(note) == 1
    semantic_artifacts = sorted((root / "sources" / "semantic").rglob("*.md"))
    assert semantic_artifacts == [existing_evidence]
