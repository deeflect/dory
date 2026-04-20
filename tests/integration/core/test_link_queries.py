from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus, reindex_paths
from dory_core.link import LinkService
from dory_core.types import WriteReq
from dory_core.write import WriteEngine


def test_link_queries_use_edges_table(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    project_target = corpus_root / "projects" / "dory" / "state.md"
    project_target.parent.mkdir(parents=True, exist_ok=True)
    project_target.write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\n---\n\nCanonical Dory state.\n",
        encoding="utf-8",
    )

    writer = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    writer.write(
        WriteReq(
            kind="append",
            target="knowledge/meeting.md",
            content="Talked to [[people/alex|Alex]] and [[knowledge/dev/rust|Rust notes]].",
            frontmatter={"title": "Meeting", "type": "knowledge"},
        )
    )

    service = LinkService(corpus_root, index_root)
    neighbors = service.neighbors("knowledge/meeting.md")
    backlinks = service.backlinks("people/alex.md")
    lint = service.lint()

    assert neighbors["count"] == 2
    assert any(edge["to"] == "people/alex.md" for edge in neighbors["edges"])
    assert any(edge["from"] == "knowledge/meeting.md" for edge in backlinks["edges"])
    assert any(edge["to"] == "knowledge/dev/rust.md" for edge in lint["broken"])


def test_write_auto_detects_known_entity_mentions(tmp_path: Path, sample_corpus_root: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    project_target = corpus_root / "projects" / "dory" / "state.md"
    project_target.parent.mkdir(parents=True, exist_ok=True)
    project_target.write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\n---\n\nCanonical Dory state.\n",
        encoding="utf-8",
    )

    writer = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    response = writer.write(
        WriteReq(
            kind="append",
            target="knowledge/meeting.md",
            content="Alex reviewed Dory deployment notes with me.",
            frontmatter={"title": "Meeting", "type": "knowledge"},
        )
    )

    service = LinkService(corpus_root, index_root)
    neighbors = service.neighbors("knowledge/meeting.md")

    assert response.edges_added >= 2
    assert any(edge["to"] == "people/alex.md" for edge in neighbors["edges"])
    assert any(edge["to"] == "projects/dory/state.md" for edge in neighbors["edges"])


def test_reindex_paths_removes_edges_for_deleted_docs(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "people").mkdir(parents=True)
    (corpus_root / "notes").mkdir(parents=True)

    (corpus_root / "people" / "alex.md").write_text(
        "---\ntitle: Alex\ntype: person\nstatus: active\n---\n\nAlex profile.\n",
        encoding="utf-8",
    )
    note_path = corpus_root / "notes" / "meeting.md"
    note_path.write_text(
        "---\ntitle: Meeting\ntype: knowledge\nstatus: done\n---\n\nTalked to [[people/alex|Alex]].\n",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    service = LinkService(corpus_root, index_root)
    assert service.backlinks("people/alex.md")["count"] == 1

    note_path.unlink()
    reindex_paths(corpus_root, index_root, fake_embedder, ["notes/meeting.md"])

    backlinks = service.backlinks("people/alex.md")
    lint = service.lint()

    assert backlinks["count"] == 0
    assert not any(item["from"] == "notes/meeting.md" for item in lint["broken"])
