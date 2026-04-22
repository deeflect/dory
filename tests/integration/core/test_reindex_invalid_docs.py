from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus


def test_reindex_skips_markdown_without_frontmatter(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    (corpus_root / "README.md").write_text("no frontmatter here\n", encoding="utf-8")

    result = reindex_corpus(corpus_root, index_root, fake_embedder)

    assert result.files_indexed == 6
    assert result.skipped_files == 1
    assert result.skipped_paths == ["README.md"]


def test_reindex_skips_markdown_with_invalid_frontmatter(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    (corpus_root / "broken.md").write_text(
        "---\n"
        "title: Broken\n"
        "created: 2026-04-07\n"
        "type: note\n"
        "not-a-frontmatter-line\n"
        "---\n"
        "bad frontmatter shape for the current parser\n",
        encoding="utf-8",
    )

    result = reindex_corpus(corpus_root, index_root, fake_embedder)

    assert result.files_indexed == 6
    assert result.skipped_files == 1
    assert result.skipped_paths == ["broken.md"]
