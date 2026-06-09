from __future__ import annotations

from pathlib import Path

import pytest

from dory_core import semantic_write_artifacts as semantic_write_artifacts_module
from dory_core import write as write_module
from dory_core.frontmatter import load_markdown_document
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.semantic_write_canonical import SemanticCanonicalPublisher
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
        project="dory",
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
        project="dory",
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


def test_semantic_write_indexes_evidence_and_rewrites_canonical_once(
    tmp_path: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (root / "projects" / "dory").mkdir(parents=True)
    project_path = root / "projects" / "dory" / "state.md"
    project_path.write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\ncanonical: true\naliases:\n  - dory\n---\n"
        "\n## Current State\n- Existing state.\n",
        encoding="utf-8",
    )
    indexed_batches: list[tuple[str, ...]] = []

    def capture_reindex_paths(root_arg, index_root_arg, embedder_arg, paths) -> None:
        assert root_arg == root
        assert index_root_arg == index_root
        assert embedder_arg is fake_embedder
        indexed_batches.append(tuple(paths))

    monkeypatch.setattr(semantic_write_artifacts_module, "reindex_paths", capture_reindex_paths)
    monkeypatch.setattr(semantic_write_artifacts_module, "load_known_entities", lambda root_arg: {})
    monkeypatch.setattr(semantic_write_artifacts_module, "sync_document_edges", lambda *args, **kwargs: 0)
    monkeypatch.setattr(write_module, "reindex_paths", capture_reindex_paths)
    monkeypatch.setattr(write_module, "load_known_entities", lambda root_arg: {})
    monkeypatch.setattr(write_module, "sync_document_edges", lambda *args, **kwargs: 0)

    engine = SemanticWriteEngine(root, index_root=index_root, embedder=fake_embedder)
    response = engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="dory",
            content="Dory records deployment state without redundant canonical rewrites.",
            scope="project",
        project="dory",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.indexed is True
    assert response.evidence_path is not None
    assert indexed_batches == [
        (response.evidence_path,),
        ("projects/dory/state.md",),
    ]
    assert response.evidence_path in project_path.read_text(encoding="utf-8")


def test_semantic_write_replay_reuses_evidence_after_claim_recording_failure(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    req = MemoryWriteReq(
        action="write",
        kind="state",
        subject="dory",
        content="Dory replay should finish after claim recording resumes.",
        scope="project",
        project="dory",
        allow_canonical=True,
    )
    engine = SemanticWriteEngine(root)
    original_recorder = engine.claim_recorder

    class FailingClaimRecorder:
        def record(self, *args, **kwargs) -> None:
            raise RuntimeError("simulated claim recorder failure")

        def sync_registry(self, *args, **kwargs) -> None:
            original_recorder.sync_registry(*args, **kwargs)

    engine.claim_recorder = FailingClaimRecorder()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="simulated claim recorder failure"):
        engine.write(req)

    semantic_artifacts = sorted((root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 1
    assert engine.claim_store.current_claims("project:dory") == ()

    engine.claim_recorder = original_recorder
    response = engine.write(req)

    assert response.result == "written"
    assert response.evidence_path == semantic_artifacts[0].relative_to(root).as_posix()
    assert len(sorted((root / "sources" / "semantic").rglob("*.md"))) == 1
    claims = engine.claim_store.current_claims("project:dory", kind="state")
    assert len(claims) == 1
    assert claims[0].evidence_path == response.evidence_path
    assert req.content in (root / "projects" / "dory" / "state.md").read_text(encoding="utf-8")


def test_semantic_write_replay_does_not_duplicate_claim_after_canonical_failure(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    req = MemoryWriteReq(
        action="write",
        kind="state",
        subject="dory",
        content="Dory replay should not duplicate active claims.",
        scope="project",
        project="dory",
        allow_canonical=True,
    )
    engine = SemanticWriteEngine(root)

    class FailingCanonicalPublisher:
        def rewrite_from_claims(self, *args, **kwargs):
            raise RuntimeError("simulated canonical publish failure")

        def rewrite_tombstone_from_claims(self, *args, **kwargs) -> None:
            return None

    engine.canonical_publisher = FailingCanonicalPublisher()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="simulated canonical publish failure"):
        engine.write(req)

    semantic_artifacts = sorted((root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 1
    assert len(engine.claim_store.current_claims("project:dory", kind="state")) == 1

    engine.canonical_publisher = SemanticCanonicalPublisher(
        root=root,
        writer=engine.writer,
        claim_store=engine.claim_store,
    )
    response = engine.write(req)

    assert response.result == "written"
    assert response.evidence_path == semantic_artifacts[0].relative_to(root).as_posix()
    assert len(sorted((root / "sources" / "semantic").rglob("*.md"))) == 1
    claims = engine.claim_store.current_claims("project:dory", kind="state")
    assert len(claims) == 1
    assert claims[0].statement == req.content
    assert claims[0].evidence_path == response.evidence_path
    assert req.content in (root / "projects" / "dory" / "state.md").read_text(encoding="utf-8")
