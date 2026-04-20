"""Group migrated corpus files into 150K-token batches for entity discovery.

Hard rule: no batch exceeds ``MAX_BATCH_TOKENS``. Grouping is "natural":
all ideas together (or in date-ordered chunks if they overflow), all
projects together, knowledge by subdirectory, daily/weekly/sessions in
date-sorted rolling windows. Archive is excluded — those files are
evidence for entities that live in the active buckets.

Each ``Batch`` carries the bucket label, ordered file list, and token
total so callers can verify nothing silently got dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dory_core.token_counting import TokenCounter, build_token_counter


MAX_BATCH_TOKENS = 150_000

# Buckets we want batched. Archive is evidence-only, not a discovery source.
_ACTIVE_BUCKETS = (
    "core",
    "people",
    "projects",
    "decisions",
    "concepts",
    "ideas",
    "knowledge",
    "references",
    "inbox",
    "logs",
    "digests",
)


@dataclass(frozen=True, slots=True)
class BatchFile:
    """A single file in a batch, with its corpus-relative path and token count."""

    relative_path: Path
    token_count: int


@dataclass(frozen=True, slots=True)
class Batch:
    label: str
    files: tuple[BatchFile, ...]
    token_total: int

    @property
    def file_count(self) -> int:
        return len(self.files)


def build_batches(
    corpus_root: Path,
    *,
    max_tokens: int = MAX_BATCH_TOKENS,
    counter: TokenCounter | None = None,
    exclude_buckets: frozenset[str] = frozenset({"archive"}),
) -> list[Batch]:
    """Return an ordered list of batches, all under ``max_tokens``.

    Files are scanned under ``corpus_root`` / bucket for each bucket in
    ``_ACTIVE_BUCKETS`` that isn't in ``exclude_buckets``. Pack order
    within a bucket follows the natural grouping described in the module
    docstring.
    """
    counter = counter or build_token_counter()
    batches: list[Batch] = []

    for bucket in _ACTIVE_BUCKETS:
        if bucket in exclude_buckets:
            continue
        bucket_root = corpus_root / bucket
        if not bucket_root.exists():
            continue

        if bucket in {"logs", "digests"}:
            batches.extend(_batch_timestamped(bucket_root, corpus_root, counter, max_tokens))
            continue

        if bucket == "knowledge":
            batches.extend(_batch_knowledge(bucket_root, corpus_root, counter, max_tokens))
            continue

        if bucket == "references":
            batches.extend(_batch_references(bucket_root, corpus_root, counter, max_tokens))
            continue

        files = _collect_files(bucket_root, corpus_root, counter)
        batches.extend(_pack(bucket, files, max_tokens))

    return batches


def _batch_timestamped(
    bucket_root: Path,
    corpus_root: Path,
    counter: TokenCounter,
    max_tokens: int,
) -> list[Batch]:
    """Walk logs/ or digests/ subdirs; sort by filename; pack by rolling window."""
    result: list[Batch] = []
    for sub in sorted(p for p in bucket_root.iterdir() if p.is_dir()):
        files = _collect_files(sub, corpus_root, counter)
        files.sort(key=lambda f: f.relative_path.name)
        label = f"{bucket_root.name}/{sub.name}"
        result.extend(_pack(label, files, max_tokens))
    return result


def _batch_knowledge(
    bucket_root: Path,
    corpus_root: Path,
    counter: TokenCounter,
    max_tokens: int,
) -> list[Batch]:
    """One batch per knowledge subfolder, packed if oversize."""
    result: list[Batch] = []
    direct_files: list[BatchFile] = []
    for entry in sorted(bucket_root.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".md":
            direct_files.append(_to_batch_file(entry, corpus_root, counter))
            continue
        if entry.is_dir():
            files = _collect_files(entry, corpus_root, counter)
            result.extend(_pack(f"knowledge/{entry.name}", files, max_tokens))
    if direct_files:
        result.extend(_pack("knowledge/_root", direct_files, max_tokens))
    return result


def _batch_references(
    bucket_root: Path,
    corpus_root: Path,
    counter: TokenCounter,
    max_tokens: int,
) -> list[Batch]:
    """Group references/ by its sub-bucket (reports, notes, tweets, etc.)."""
    result: list[Batch] = []
    for entry in sorted(bucket_root.iterdir()):
        if entry.is_dir():
            files = _collect_files(entry, corpus_root, counter)
            result.extend(_pack(f"references/{entry.name}", files, max_tokens))
    return result


def _collect_files(
    root: Path,
    corpus_root: Path,
    counter: TokenCounter,
) -> list[BatchFile]:
    files: list[BatchFile] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        files.append(_to_batch_file(path, corpus_root, counter))
    return files


def _to_batch_file(
    path: Path,
    corpus_root: Path,
    counter: TokenCounter,
) -> BatchFile:
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = counter.count(text)
    return BatchFile(
        relative_path=path.relative_to(corpus_root),
        token_count=tokens,
    )


def _pack(
    label: str,
    files: Iterable[BatchFile],
    max_tokens: int,
) -> list[Batch]:
    """Pack files into batches under ``max_tokens``. Pass through empty input."""
    files_list = list(files)
    if not files_list:
        return []

    batches: list[Batch] = []
    current: list[BatchFile] = []
    current_total = 0
    shard = 1

    for batch_file in files_list:
        file_tokens = batch_file.token_count
        # Oversize single file: emit as its own (oversize) batch and move on.
        if file_tokens > max_tokens:
            if current:
                batches.append(_emit(label, current, current_total, shard))
                shard += 1
                current = []
                current_total = 0
            batches.append(
                Batch(
                    label=f"{label}#{shard}-oversize",
                    files=(batch_file,),
                    token_total=file_tokens,
                )
            )
            shard += 1
            continue
        if current_total + file_tokens > max_tokens and current:
            batches.append(_emit(label, current, current_total, shard))
            shard += 1
            current = []
            current_total = 0
        current.append(batch_file)
        current_total += file_tokens

    if current:
        batches.append(_emit(label, current, current_total, shard))

    return batches


def _emit(label: str, files: list[BatchFile], total: int, shard: int) -> Batch:
    return Batch(
        label=f"{label}#{shard}",
        files=tuple(files),
        token_total=total,
    )


def format_batching_summary(batches: list[Batch]) -> dict[str, object]:
    total_files = sum(b.file_count for b in batches)
    total_tokens = sum(b.token_total for b in batches)
    oversize = sum(1 for b in batches if b.token_total > MAX_BATCH_TOKENS)
    by_label: dict[str, int] = {}
    for batch in batches:
        key = batch.label.split("#", 1)[0]
        by_label[key] = by_label.get(key, 0) + batch.file_count
    return {
        "total_batches": len(batches),
        "total_files": total_files,
        "total_tokens": total_tokens,
        "oversize_batches": oversize,
        "files_per_group": dict(sorted(by_label.items(), key=lambda kv: -kv[1])),
    }
