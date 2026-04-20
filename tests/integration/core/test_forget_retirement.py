from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.types import MemoryWriteReq, SearchReq


def _seed_corpus(root: Path) -> Path:
    (root / "people").mkdir(parents=True)
    person_path = root / "people" / "alex-example.md"
    person_path.write_text(
        "---\n"
        "title: Alex Example\n"
        "aliases:\n"
        "  - anna\n"
        "type: person\n"
        "status: active\n"
        "canonical: true\n"
        "---\n"
        "# Anna\n"
        "\n"
        "## Summary\n"
        "\n"
        "Initial summary.\n",
        encoding="utf-8",
    )
    return person_path


def test_forget_retires_original_from_search_results(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    person_path = _seed_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)

    engine = SemanticWriteEngine(corpus_root, index_root=index_root, embedder=fake_embedder)
    engine.write(
        MemoryWriteReq(
            action="write",
            kind="fact",
            subject="anna",
            content="Prefers async work.",
            scope="person",
            allow_canonical=True,
        )
    )
    engine.write(
        MemoryWriteReq(
            action="forget",
            kind="note",
            subject="anna",
            content="Old preference note should be removed.",
            scope="person",
            reason="no longer valid",
            allow_canonical=True,
        )
    )

    person_document = load_markdown_document(person_path.read_text(encoding="utf-8"))
    assert person_document.frontmatter["status"] == "superseded"
    assert person_document.frontmatter["canonical"] is False

    response = SearchEngine(index_root, fake_embedder).search(
        SearchReq(query="Prefers async work", mode="hybrid")
    )

    assert all(result.path != "people/alex-example.md" for result in response.results)
