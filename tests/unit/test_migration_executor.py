from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.migration_executor import execute_source_tree


def _write(source_root: Path, relative: str, content: str) -> Path:
    path = source_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_executor_writes_routed_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(
        source,
        "active/projects/borb-bot.md",
        "---\ntitle: borb-bot\ntype: project\nstatus: active\n---\n\nbody\n",
    )

    report = execute_source_tree(source, corpus)

    assert report.written == 1
    assert (corpus / "projects" / "borb-bot" / "state.md").exists()


def test_executor_applies_archive_tombstone(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(
        source,
        "archive/projects/burnrate.md",
        "---\ntitle: burnrate legacy\ntype: project\nstatus: active\n---\n\nold notes\n",
    )

    report = execute_source_tree(source, corpus)

    assert report.written == 1
    target = corpus / "archive" / "projects" / "burnrate.md"
    assert target.exists()

    doc = load_markdown_document(target.read_text(encoding="utf-8"))
    assert doc.frontmatter["canonical"] is False
    assert doc.frontmatter["status"] == "superseded"
    assert doc.frontmatter["source_kind"] == "legacy"
    assert doc.frontmatter["temperature"] == "cold"


def test_executor_synthesizes_frontmatter_for_bare_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(source, "inbox/overnight-research/wave-1.md", "Just a bare file, no frontmatter.\n")

    report = execute_source_tree(source, corpus)

    assert report.written == 1
    target = corpus / "inbox" / "overnight-research" / "wave-1.md"
    assert target.exists()

    doc = load_markdown_document(target.read_text(encoding="utf-8"))
    assert doc.frontmatter["title"] != ""
    assert doc.frontmatter["type"] == "capture"
    assert doc.frontmatter["status"] == "raw"
    assert "Just a bare file" in doc.body


def test_executor_skips_exclude_and_review_by_default(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(source, "media/images/borb.png", "binary")
    _write(source, "system/dreams/x.md", "---\ntitle: x\ntype: note\n---\n\n")
    _write(
        source,
        "active/projects/_supporting/weird-nested/nested.md",
        "---\ntitle: nested\ntype: project\n---\n\n",
    )

    report = execute_source_tree(source, corpus)

    assert report.written == 0
    assert report.skipped >= 2
    assert not (corpus).exists() or not list(corpus.rglob("*.md"))


def test_executor_dry_run_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(
        source,
        "active/daily/2026-04-10.md",
        "---\ntitle: 2026-04-10\ntype: daily\nstatus: done\ndate: 2026-04-10\n---\n\nbody\n",
    )

    report = execute_source_tree(source, corpus, dry_run=True)

    assert report.written == 1
    assert not (corpus).exists() or not list(corpus.rglob("*.md"))


def test_executor_limit_caps_processed_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    for i in range(5):
        _write(
            source,
            f"active/ideas/2026-02-{i:02}-idea.md",
            f"---\ntitle: idea {i}\ntype: idea\nstatus: pending\n---\n\nbody\n",
        )

    report = execute_source_tree(source, corpus, limit=3)

    assert report.total_decisions == 3
    assert report.written == 3


def test_executor_fills_missing_type_from_destination(tmp_path: Path) -> None:
    source = tmp_path / "src"
    corpus = tmp_path / "corpus"
    _write(
        source,
        "active/projects/rooster-spec.md",
        "---\ntitle: Rooster\n---\n\nproject body\n",
    )

    report = execute_source_tree(source, corpus)

    assert report.written == 1
    target = corpus / "projects" / "rooster-spec" / "state.md"
    doc = load_markdown_document(target.read_text(encoding="utf-8"))
    assert doc.frontmatter["type"] == "project"
