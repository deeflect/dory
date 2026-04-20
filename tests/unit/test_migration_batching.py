from __future__ import annotations

from pathlib import Path

from dory_core.migration_batching import (
    MAX_BATCH_TOKENS,
    Batch,
    BatchFile,
    build_batches,
    format_batching_summary,
)


class _FixedCounter:
    """Token counter that returns a stored count per text payload."""

    def __init__(self, per_file: int) -> None:
        self.per_file = per_file

    def count(self, text: str, *, agent: str = "default") -> int:
        return self.per_file


def _write(path: Path, content: str = "body\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_ideas_bucket_packs_into_one_batch(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    for i in range(10):
        _write(corpus / "ideas" / f"2026-02-{i:02}-idea.md")
    counter = _FixedCounter(per_file=1_000)

    batches = build_batches(corpus, counter=counter)
    ideas_batches = [b for b in batches if b.label.startswith("ideas")]

    assert len(ideas_batches) == 1
    assert ideas_batches[0].file_count == 10


def test_oversize_bucket_splits_into_multiple_batches(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    for i in range(4):
        _write(corpus / "ideas" / f"idea-{i}.md")
    counter = _FixedCounter(per_file=60_000)  # 2 files per 150K batch

    batches = build_batches(corpus, counter=counter)
    ideas_batches = [b for b in batches if b.label.startswith("ideas")]

    assert len(ideas_batches) == 2
    for batch in ideas_batches:
        assert batch.token_total <= MAX_BATCH_TOKENS
    total_files = sum(b.file_count for b in ideas_batches)
    assert total_files == 4


def test_logs_daily_batches_respect_date_order(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    for date in ["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"]:
        _write(corpus / "logs" / "daily" / f"{date}.md")
    counter = _FixedCounter(per_file=50_000)

    batches = build_batches(corpus, counter=counter)
    daily_batches = [b for b in batches if b.label.startswith("logs/daily")]

    assert len(daily_batches) >= 2
    all_stems: list[str] = []
    for batch in daily_batches:
        for bf in batch.files:
            all_stems.append(bf.relative_path.stem)
    assert all_stems == sorted(all_stems)


def test_knowledge_batches_per_subfolder(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "knowledge" / "ai" / "graph.md")
    _write(corpus / "knowledge" / "ai" / "models.md")
    _write(corpus / "knowledge" / "business" / "clients.md")
    _write(corpus / "knowledge" / "dev" / "stack.md")
    counter = _FixedCounter(per_file=10_000)

    batches = build_batches(corpus, counter=counter)
    labels = sorted(b.label.split("#")[0] for b in batches if b.label.startswith("knowledge"))

    assert "knowledge/ai" in labels
    assert "knowledge/business" in labels
    assert "knowledge/dev" in labels


def test_archive_is_excluded_by_default(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "archive" / "projects" / "old.md")
    _write(corpus / "projects" / "new" / "state.md")
    counter = _FixedCounter(per_file=1_000)

    batches = build_batches(corpus, counter=counter)

    for batch in batches:
        assert not batch.label.startswith("archive")


def test_oversize_single_file_emits_oversize_batch(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "ideas" / "huge.md")
    _write(corpus / "ideas" / "small.md")
    counter = _FixedCounter(per_file=200_000)

    batches = build_batches(corpus, counter=counter)
    labels = [b.label for b in batches if b.label.startswith("ideas")]

    assert any("oversize" in label for label in labels)


def test_summary_totals_are_accurate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "people" / "casey.md")
    _write(corpus / "projects" / "foo" / "state.md")
    counter = _FixedCounter(per_file=100)

    batches = build_batches(corpus, counter=counter)
    summary = format_batching_summary(batches)

    assert summary["total_files"] == 2
    assert summary["total_tokens"] == 200


def test_empty_corpus_returns_no_batches(tmp_path: Path) -> None:
    corpus = tmp_path / "empty"
    corpus.mkdir()
    counter = _FixedCounter(per_file=0)

    assert build_batches(corpus, counter=counter) == []
