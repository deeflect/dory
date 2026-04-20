from __future__ import annotations

from pathlib import Path

from dory_core.link import LinkService
from dory_core.types import WriteReq
from dory_core.write import WriteEngine


def test_neighbors_honors_depth(tmp_path: Path, sample_corpus_root: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    writer = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    writer.write(
        WriteReq(
            kind="append",
            target="knowledge/first.md",
            content="See [[knowledge/second|Second]].",
            frontmatter={"title": "First", "type": "knowledge"},
        )
    )
    writer.write(
        WriteReq(
            kind="append",
            target="knowledge/second.md",
            content="See [[people/alex|Alex]].",
            frontmatter={"title": "Second", "type": "knowledge"},
        )
    )

    service = LinkService(corpus_root, index_root)
    shallow = service.neighbors("knowledge/first.md", depth=1)
    deep = service.neighbors("knowledge/first.md", depth=2)

    assert shallow["count"] == 1
    assert shallow["total_count"] == 1
    assert shallow["truncated"] is False
    assert any(edge["to"] == "knowledge/second.md" for edge in shallow["edges"])
    assert any(edge["to"] == "people/alex.md" for edge in deep["edges"])


def test_neighbors_caps_and_filters_edges(tmp_path: Path, sample_corpus_root: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    writer = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    writer.write(
        WriteReq(
            kind="append",
            target="knowledge/links.md",
            content=(
                "See [[knowledge/one|One]], [[knowledge/two|Two]], "
                "[[knowledge/three|Three]], and [[people/alex|Alex]]."
            ),
            frontmatter={"title": "Links", "type": "knowledge"},
        )
    )

    service = LinkService(corpus_root, index_root)
    capped = service.neighbors("knowledge/links.md", max_edges=2)
    filtered = service.neighbors("knowledge/links.md", exclude_prefixes=("people/",))

    assert capped["count"] == 2
    assert capped["total_count"] == 4
    assert capped["truncated"] is True
    assert filtered["count"] == 3
    assert filtered["total_count"] == 3
    assert all(not edge["to"].startswith("people/") for edge in filtered["edges"])
