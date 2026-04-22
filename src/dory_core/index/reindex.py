from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import logging
from pathlib import Path
import sqlite3
from time import monotonic

from dory_core.chunking import chunk_markdown
from dory_core.embedding import ContentEmbedder
from dory_core.frontmatter import load_markdown_document
from dory_core.index.json_vector_store import VectorRecord
from dory_core.index.sqlite_store import SqliteStore
from dory_core.index.sqlite_vector_store import SqliteVectorStore
from dory_core.link import load_known_entities, sync_document_edges
from dory_core.markdown_store import MarkdownDocument, MarkdownStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReindexProgress:
    phase: str
    processed: int
    total: int
    message: str = ""
    elapsed_s: float = 0.0
    rate: float | None = None
    eta_s: float | None = None


ReindexProgressCallback = Callable[[ReindexProgress], None]


@dataclass(frozen=True, slots=True)
class ReindexResult:
    files_indexed: int
    chunks_indexed: int
    vectors_indexed: int
    skipped_files: int = 0
    skipped_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    new_paths: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    orphan_paths: list[str] = field(default_factory=list)
    unchanged_count: int = 0
    embedding_model_changed: bool = False

    @property
    def affected_paths(self) -> list[str]:
        return [*self.new_paths, *self.changed_paths, *self.orphan_paths]

    @property
    def is_empty(self) -> bool:
        return not self.affected_paths and not self.embedding_model_changed


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    plan: ReconcilePlan
    files_indexed: int
    chunks_indexed: int
    vectors_indexed: int
    orphans_removed: int
    skipped_paths: list[str] = field(default_factory=list)


class _ProgressTracker:
    """Tracks elapsed/rate/eta on a per-phase basis so callbacks carry useful signal."""

    def __init__(self) -> None:
        self._run_start = monotonic()
        self._phase: str | None = None
        self._phase_start = self._run_start

    def observe(self, phase: str, processed: int, total: int) -> tuple[float, float | None, float | None]:
        now = monotonic()
        if phase != self._phase:
            self._phase = phase
            self._phase_start = now
        elapsed = now - self._run_start
        phase_elapsed = max(now - self._phase_start, 1e-9)
        rate: float | None = None
        eta: float | None = None
        if processed > 0:
            rate = processed / phase_elapsed
            if total > processed and rate > 0:
                eta = (total - processed) / rate
        return elapsed, rate, eta


@dataclass(frozen=True, slots=True)
class _IndexRuntime:
    sqlite_store: SqliteStore
    vector_store: SqliteVectorStore
    current_embedding_model: str
    current_embedding_dimensions: str
    existing_cache: dict[str, str]
    existing_vectors: dict[str, VectorRecord]


@dataclass(frozen=True, slots=True)
class _PreparedRows:
    file_rows: list[dict[str, object]]
    chunk_rows: list[dict[str, object]]
    vector_rows: list[VectorRecord]
    embedding_cache: dict[str, str]


def reindex_corpus(
    root: Path,
    index_root: Path,
    embedder: ContentEmbedder,
    *,
    progress: ReindexProgressCallback | None = None,
) -> ReindexResult:
    tracker = _ProgressTracker()
    _emit_progress(progress, tracker, phase="scan", processed=0, total=0, message=f"scanning {root}")
    scan = MarkdownStore().scan(root)
    total_docs = len(scan.documents)
    _emit_progress(
        progress,
        tracker,
        phase="scan",
        processed=total_docs,
        total=total_docs,
        message=f"discovered {total_docs} markdown files",
    )
    runtime = _build_index_runtime(index_root, embedder)
    prepared = _prepare_rows(scan.documents, runtime=runtime, embedder=embedder, progress=progress, tracker=tracker)
    _emit_progress(progress, tracker, phase="persist", processed=0, total=total_docs, message="replacing sqlite index")
    runtime.sqlite_store.replace_documents(
        prepared.file_rows,
        prepared.chunk_rows,
        embedding_cache=prepared.embedding_cache,
        meta=_runtime_meta(runtime),
    )
    _emit_progress(progress, tracker, phase="persist", processed=total_docs, total=total_docs, message="replacing vector index")
    vectors_indexed = runtime.vector_store.replace(prepared.vector_rows)
    _emit_progress(progress, tracker, phase="links", processed=0, total=total_docs, message="resyncing links")
    _replace_all_edges(root=root, db_path=runtime.sqlite_store.db_path, documents=scan.documents)
    _emit_progress(progress, tracker, phase="done", processed=total_docs, total=total_docs, message="reindex complete")

    return ReindexResult(
        files_indexed=len(prepared.file_rows),
        chunks_indexed=len(prepared.chunk_rows),
        vectors_indexed=vectors_indexed,
        skipped_files=len(scan.skipped_paths),
        skipped_paths=scan.skipped_paths,
    )


def reindex_paths(
    root: Path,
    index_root: Path,
    embedder: ContentEmbedder,
    relative_paths: list[str] | tuple[str, ...],
    *,
    progress: ReindexProgressCallback | None = None,
    tracker: _ProgressTracker | None = None,
) -> ReindexResult:
    owns_tracker = tracker is None
    if owns_tracker:
        tracker = _ProgressTracker()
    runtime = _build_index_runtime(index_root, embedder)
    documents = []
    skipped_paths: list[str] = []
    affected_paths = sorted({str(Path(path).as_posix()) for path in relative_paths})

    for relative_path in affected_paths:
        document = _load_single_document(root, Path(relative_path))
        if document is None:
            skipped_paths.append(relative_path)
            continue
        documents.append(document)

    _emit_progress(
        progress,
        tracker,
        phase="scan",
        processed=len(documents),
        total=len(affected_paths),
        message=f"loaded {len(documents)} changed paths",
    )
    prepared = _prepare_rows(documents, runtime=runtime, embedder=embedder, progress=progress, tracker=tracker)
    stale_chunk_ids = runtime.sqlite_store.load_chunk_ids_for_paths(affected_paths)
    _emit_progress(
        progress,
        tracker,
        phase="persist",
        processed=0,
        total=len(affected_paths),
        message="upserting sqlite index",
    )
    runtime.vector_store.delete_many(stale_chunk_ids)
    runtime.sqlite_store.upsert_documents(
        prepared.file_rows,
        prepared.chunk_rows,
        delete_paths=affected_paths,
        embedding_cache=prepared.embedding_cache,
        meta=_runtime_meta(runtime),
    )
    _emit_progress(
        progress,
        tracker,
        phase="persist",
        processed=len(affected_paths),
        total=len(affected_paths),
        message="upserting vectors",
    )
    vectors_indexed = runtime.vector_store.upsert(prepared.vector_rows)
    _emit_progress(progress, tracker, phase="links", processed=0, total=len(documents), message="resyncing links")
    _resync_edges_for_paths(
        root=root,
        db_path=runtime.sqlite_store.db_path,
        documents=documents,
        deleted_paths=skipped_paths,
    )
    if owns_tracker:
        _emit_progress(
            progress,
            tracker,
            phase="done",
            processed=len(documents),
            total=len(affected_paths),
            message="path reindex complete",
        )

    return ReindexResult(
        files_indexed=len(prepared.file_rows),
        chunks_indexed=len(prepared.chunk_rows),
        vectors_indexed=vectors_indexed,
        skipped_files=len(skipped_paths),
        skipped_paths=skipped_paths,
    )


def plan_reconcile(
    root: Path,
    index_root: Path,
    embedder: ContentEmbedder,
) -> ReconcilePlan:
    """Compare the on-disk corpus to the index and classify the delta.

    Does not mutate the index. Safe to call as a dry-run.
    """

    scan = MarkdownStore().scan(root)
    disk_hashes = {str(doc.path.as_posix()): doc.hash for doc in scan.documents}

    sqlite_store = SqliteStore(index_root / "dory.db")
    indexed_hashes = sqlite_store.load_file_hashes()
    existing_meta = sqlite_store.load_meta()
    current_model = str(getattr(embedder, "model", "unknown"))
    current_dim = str(embedder.dimension)
    model_changed = bool(indexed_hashes) and (
        existing_meta.get("embedding_model") != current_model
        or existing_meta.get("embedding_dimensions") != current_dim
    )

    new_paths: list[str] = []
    changed_paths: list[str] = []
    unchanged = 0
    for path, disk_hash in disk_hashes.items():
        indexed_hash = indexed_hashes.get(path)
        if indexed_hash is None:
            new_paths.append(path)
        elif indexed_hash != disk_hash:
            changed_paths.append(path)
        else:
            unchanged += 1

    orphan_paths = sorted(set(indexed_hashes) - set(disk_hashes))

    return ReconcilePlan(
        new_paths=sorted(new_paths),
        changed_paths=sorted(changed_paths),
        orphan_paths=orphan_paths,
        unchanged_count=unchanged,
        embedding_model_changed=model_changed,
    )


def reconcile_corpus(
    root: Path,
    index_root: Path,
    embedder: ContentEmbedder,
    *,
    batch_size: int = 200,
    progress: ReindexProgressCallback | None = None,
) -> ReconcileResult:
    """Sync the index to the current corpus state with only the minimum work.

    Runs the delta through `reindex_paths` in batches so an interrupt only
    loses the current batch — a rerun naturally resumes.
    """

    tracker = _ProgressTracker()
    plan = plan_reconcile(root, index_root, embedder)

    if plan.embedding_model_changed:
        _emit_progress(
            progress,
            tracker,
            phase="plan",
            processed=0,
            total=1,
            message="embedding model changed — falling back to full rebuild",
        )
        result = reindex_corpus(root, index_root, embedder, progress=progress)
        return ReconcileResult(
            plan=plan,
            files_indexed=result.files_indexed,
            chunks_indexed=result.chunks_indexed,
            vectors_indexed=result.vectors_indexed,
            orphans_removed=0,
            skipped_paths=result.skipped_paths,
        )

    targets = plan.affected_paths
    total = len(targets)
    _emit_progress(
        progress,
        tracker,
        phase="plan",
        processed=0,
        total=total,
        message=(
            f"{len(plan.new_paths)} new, {len(plan.changed_paths)} changed, "
            f"{len(plan.orphan_paths)} orphans, {plan.unchanged_count} unchanged"
        ),
    )

    files_indexed = 0
    chunks_indexed = 0
    vectors_indexed = 0
    skipped_paths: list[str] = []
    batch_size = max(1, batch_size)
    for start in range(0, total, batch_size):
        batch = targets[start : start + batch_size]
        _emit_progress(
            progress,
            tracker,
            phase="batch",
            processed=start,
            total=total,
            message=f"batch {start // batch_size + 1} ({len(batch)} files)",
        )
        result = reindex_paths(root, index_root, embedder, batch, progress=progress, tracker=tracker)
        files_indexed += result.files_indexed
        chunks_indexed += result.chunks_indexed
        vectors_indexed += result.vectors_indexed
        skipped_paths.extend(result.skipped_paths)

    _emit_progress(
        progress,
        tracker,
        phase="done",
        processed=total,
        total=total,
        message="reconcile complete",
    )

    return ReconcileResult(
        plan=plan,
        files_indexed=files_indexed,
        chunks_indexed=chunks_indexed,
        vectors_indexed=vectors_indexed,
        orphans_removed=len(plan.orphan_paths),
        skipped_paths=skipped_paths,
    )


def _build_index_runtime(index_root: Path, embedder: ContentEmbedder) -> _IndexRuntime:
    sqlite_store = SqliteStore(index_root / "dory.db")
    vector_store = SqliteVectorStore(index_root / "dory.db", dimension=embedder.dimension)
    vector_store.import_legacy_json_if_empty(index_root / "lance")
    current_embedding_model = str(getattr(embedder, "model", "unknown"))
    current_embedding_dimensions = str(embedder.dimension)
    existing_meta = sqlite_store.load_meta()
    cache_is_compatible = (
        existing_meta.get("embedding_model") == current_embedding_model
        and existing_meta.get("embedding_dimensions") == current_embedding_dimensions
    )
    existing_cache = sqlite_store.load_embedding_cache() if cache_is_compatible else {}
    existing_vectors = {record.chunk_id: record for record in vector_store.all()} if cache_is_compatible else {}
    return _IndexRuntime(
        sqlite_store=sqlite_store,
        vector_store=vector_store,
        current_embedding_model=current_embedding_model,
        current_embedding_dimensions=current_embedding_dimensions,
        existing_cache=existing_cache,
        existing_vectors=existing_vectors,
    )


def _prepare_rows(
    documents: list[object],
    *,
    runtime: _IndexRuntime,
    embedder: ContentEmbedder,
    progress: ReindexProgressCallback | None,
    tracker: _ProgressTracker,
) -> _PreparedRows:
    file_rows: list[dict[str, object]] = []
    chunk_rows: list[dict[str, object]] = []
    chunk_specs: list[tuple[str, str]] = []
    resolved_vectors: dict[str, list[float]] = {}
    pending_vectors: dict[str, str] = {}
    embedding_cache: dict[str, str] = {}

    total_documents = len(documents)
    for document_index, document in enumerate(documents, start=1):
        if document_index == 1 or document_index == total_documents or document_index % 100 == 0:
            _emit_progress(
                progress,
                tracker,
                phase="prepare",
                processed=document_index,
                total=total_documents,
                message=f"prepared {document_index}/{total_documents} files",
            )
        file_rows.append(
            {
                "path": str(document.path),
                "hash": document.hash,
                "mtime": document.mtime,
                "size": document.size,
                "frontmatter": document.frontmatter,
            }
        )

        for chunk in document.chunks:
            content_hash = f"sha256:{sha256(chunk.content.encode('utf-8')).hexdigest()}"
            chunk_id = f"{document.path}#{chunk.chunk_index}"
            chunk_rows.append(
                {
                    "chunk_id": chunk_id,
                    "path": str(document.path),
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "hash": content_hash,
                    "frontmatter": document.frontmatter,
                }
            )
            chunk_specs.append((chunk_id, content_hash))
            embedding_cache.setdefault(content_hash, chunk_id)

            cached_vector_id = runtime.existing_cache.get(content_hash)
            if cached_vector_id is not None:
                cached_record = runtime.existing_vectors.get(cached_vector_id)
                if cached_record is not None and len(cached_record.vector) == embedder.dimension:
                    resolved_vectors.setdefault(content_hash, cached_record.vector)

            if content_hash not in resolved_vectors:
                pending_vectors.setdefault(content_hash, chunk.content)

    pending_items = list(pending_vectors.items())
    batch_size = max(1, int(getattr(embedder, "batch_size", len(pending_items) or 1)))
    total_batches = (len(pending_items) + batch_size - 1) // batch_size if pending_items else 0
    for batch_index, start in enumerate(range(0, len(pending_items), batch_size), start=1):
        batch = pending_items[start : start + batch_size]
        _emit_progress(
            progress,
            tracker,
            phase="embed",
            processed=batch_index,
            total=total_batches,
            message=f"embedding batch {batch_index}/{total_batches} ({len(batch)} chunks)",
        )
        vectors = embedder.embed([content for _, content in batch])
        for (content_hash, _), vector in zip(batch, vectors, strict=True):
            resolved_vectors[content_hash] = vector

    vector_rows = [
        VectorRecord(
            chunk_id=chunk_id,
            content_hash=content_hash,
            vector=resolved_vectors[content_hash],
        )
        for chunk_id, content_hash in chunk_specs
    ]
    return _PreparedRows(
        file_rows=file_rows,
        chunk_rows=chunk_rows,
        vector_rows=vector_rows,
        embedding_cache=embedding_cache,
    )


def _emit_progress(
    callback: ReindexProgressCallback | None,
    tracker: _ProgressTracker,
    *,
    phase: str,
    processed: int,
    total: int,
    message: str,
) -> None:
    elapsed, rate, eta = tracker.observe(phase, processed, total)
    progress = ReindexProgress(
        phase=phase,
        processed=processed,
        total=total,
        message=message,
        elapsed_s=elapsed,
        rate=rate,
        eta_s=eta,
    )
    _logger.info("reindex %s %s/%s %s", progress.phase, progress.processed, progress.total, progress.message)
    if callback is not None:
        callback(progress)


def _load_single_document(root: Path, relative_path: Path) -> MarkdownDocument | None:
    target = root / relative_path
    if not target.exists():
        return None
    if target.suffix.lower() != ".md":
        return None
    text = target.read_text(encoding="utf-8")
    try:
        parsed = load_markdown_document(text)
    except ValueError:
        return None
    stat = target.stat()
    return MarkdownDocument(
        path=relative_path,
        frontmatter=parsed.frontmatter,
        content=text,
        hash=f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
        size=len(text.encode("utf-8")),
        mtime=str(int(stat.st_mtime)),
        chunks=chunk_markdown(text),
    )


def _runtime_meta(runtime: _IndexRuntime) -> dict[str, str]:
    return {
        "embedding_dimensions": runtime.current_embedding_dimensions,
        "embedding_model": runtime.current_embedding_model,
        "last_reindex_at": datetime.now(UTC).isoformat(),
    }


def _replace_all_edges(*, root: Path, db_path: Path, documents: list[MarkdownDocument]) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM edges")
        connection.commit()
    _sync_document_edges(root=root, db_path=db_path, documents=documents)


def _resync_edges_for_paths(
    *,
    root: Path,
    db_path: Path,
    documents: list[MarkdownDocument],
    deleted_paths: list[str],
) -> None:
    existing_paths = [str(document.path) for document in documents]
    with sqlite3.connect(db_path) as connection:
        for path in existing_paths:
            connection.execute("DELETE FROM edges WHERE from_path = ?", (path,))
        for path in deleted_paths:
            connection.execute("DELETE FROM edges WHERE from_path = ? OR to_path = ?", (path, path))
        connection.commit()
    _sync_document_edges(root=root, db_path=db_path, documents=documents)


def _sync_document_edges(*, root: Path, db_path: Path, documents: list[MarkdownDocument]) -> None:
    known_entities = load_known_entities(root)
    for document in documents:
        sync_document_edges(
            db_path,
            from_path=str(document.path),
            markdown=document.content,
            known_entities=known_entities,
        )
