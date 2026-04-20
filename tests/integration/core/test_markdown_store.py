from __future__ import annotations

from pathlib import Path

from dory_core.markdown_store import MarkdownStore


def test_markdown_store_walks_fixture_corpus() -> None:
    root = Path("tests/fixtures/dory_sample")

    documents = MarkdownStore().walk(root)

    assert len(documents) == 7
    assert documents[0].path == Path("core/active.md")
    assert any(doc.path == Path("core/user.md") for doc in documents)


def test_markdown_store_parses_frontmatter() -> None:
    root = Path("tests/fixtures/dory_sample")

    documents = MarkdownStore().walk(root)
    user_doc = next(doc for doc in documents if doc.path == Path("core/user.md"))

    assert user_doc.frontmatter["title"] == "User"


def test_markdown_store_skips_markdown_without_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "README.md").write_text("# corpus notes\n", encoding="utf-8")
    (root / "note.md").write_text(
        "---\ntitle: Note\ntype: knowledge\n---\n\nBody.\n",
        encoding="utf-8",
    )

    documents = MarkdownStore().walk(root)

    assert [doc.path for doc in documents] == [Path("note.md")]
