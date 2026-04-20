from __future__ import annotations

import json
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_cli.main import app


def _copy_sample_corpus(sample_corpus_root: Path, corpus_root: Path) -> None:
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def test_cli_memory_write_routes_to_existing_subject(
    cli_runner,
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _copy_sample_corpus(sample_corpus_root, corpus_root)

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "memory-write",
            "Alex is now tracking memory routing.",
            "--subject",
            "alex",
            "--action",
            "write",
            "--kind",
            "fact",
            "--allow-canonical",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["resolved"] is True
    assert payload["result"] == "written"
    assert payload["target_path"] == "people/alex.md"
    assert "Alex is now tracking memory routing." in (corpus_root / "people" / "alex.md").read_text(encoding="utf-8")


def test_cli_memory_write_quarantines_unknown_subjects(
    cli_runner,
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _copy_sample_corpus(sample_corpus_root, corpus_root)

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "memory-write",
            "This should not be written.",
            "--subject",
            "completely unrelated subject",
            "--soft",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["resolved"] is False
    assert payload["result"] == "quarantined"
    assert payload["quarantined"] is True
    assert payload["target_path"] is not None
    assert payload["message"] is not None
    quarantine_path = corpus_root / payload["target_path"]
    assert quarantine_path.exists()
    rendered = quarantine_path.read_text(encoding="utf-8")
    assert "This should not be written." in rendered
    assert "quarantine_reason:" in rendered


def test_cli_memory_write_forget_retires_existing_subject(
    cli_runner,
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _copy_sample_corpus(sample_corpus_root, corpus_root)

    write_result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "memory-write",
            "Alex is now tracking memory routing.",
            "--subject",
            "alex",
            "--action",
            "write",
            "--kind",
            "fact",
            "--allow-canonical",
        ],
    )
    assert write_result.exit_code == 0, write_result.output

    forget_result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "memory-write",
            "Alex is now tracking memory routing.",
            "--subject",
            "alex",
            "--action",
            "forget",
            "--kind",
            "fact",
            "--reason",
            "superseded",
            "--allow-canonical",
        ],
    )

    assert forget_result.exit_code == 0, forget_result.output
    payload = json.loads(forget_result.stdout)
    assert payload["resolved"] is True
    assert payload["result"] == "forgotten"
    semantic_artifacts = sorted((corpus_root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 2
    artifact_frontmatters = [
        load_markdown_document(path.read_text(encoding="utf-8")).frontmatter for path in semantic_artifacts
    ]
    assert {frontmatter["action"] for frontmatter in artifact_frontmatters} == {
        "write",
        "forget",
    }
    assert all(frontmatter["source_kind"] == "semantic" for frontmatter in artifact_frontmatters)
    rendered = (corpus_root / "people" / "alex.md").read_text(encoding="utf-8")
    assert "status: superseded" in rendered
