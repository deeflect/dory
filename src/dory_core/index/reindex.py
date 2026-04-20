from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import sqlite3

from dory_core.chunking import chunk_markdown
from dory_core.embedding import ContentEmbedder
from dory_core.frontmatter import load_markdown_document
from dory_core.index.json_vector_store import VectorRecord
from dory_core.index.sqlite_store import SqliteStore
from dory_core.index.sqlite_vector_store import SqliteVectorStore
from dory_core.link import load_known_entities, sync_document_edges
from dory_core.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True, slots=True)
class ReindexResult:
    files_indexed: int
    chunks_indexed: int
    vectors_indexed: int
    skipped_files: int = 0
    skipped_paths: list[str] = field(default_factory=list)


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


def reindex_corpus(root: Path, index_root: Path, embedder: ContentEmbedder) -> ReindexResult:
    scan = MarkdownStore().scan(root)
    runtime = _build_index_runtime(index_root, embedder)
    prepared = _prepare_rows(scan.documents, runtime=runtime, embedder=embedder)
    runtime.sqlite_store.replace_documents(
        prepared.file_rows,
        prepared.chunk_rows,
        embedding_cache=prepared.embedding_cache,
        meta=_runtime_meta(runtime),
    )
    vectors_indexed = runtime.vector_store.replace(prepared.vector_rows)
    _replace_all_edges(root=root, db_path=runtime.sqlite_store.db_path, documents=scan.documents)

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
) -> ReindexResult:
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

    stale_chunk_ids = runtime.sqlite_store.load_chunk_ids_for_paths(affected_paths)
    runtime.vector_store.delete_many(stale_chunk_ids)

    prepared = _prepare_rows(documents, runtime=runtime, embedder=embedder)
    runtime.sqlite_store.upsert_documents(
        prepared.file_rows,
        prepared.chunk_rows,
        delete_paths=affected_paths,
        embedding_cache=prepared.embedding_cache,
        meta=_runtime_meta(runtime),
    )
    vectors_indexed = runtime.vector_store.upsert(prepared.vector_rows)
    _resync_edges_for_paths(
        root=root,
        db_path=runtime.sqlite_store.db_path,
        documents=documents,
        deleted_paths=skipped_paths,
    )

    return ReindexResult(
        files_indexed=len(prepared.file_rows),
        chunks_indexed=len(prepared.chunk_rows),
        vectors_indexed=vectors_indexed,
        skipped_files=len(skipped_paths),
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
) -> _PreparedRows:
    file_rows: list[dict[str, object]] = []
    chunk_rows: list[dict[str, object]] = []
    chunk_specs: list[tuple[str, str]] = []
    resolved_vectors: dict[str, list[float]] = {}
    pending_vectors: dict[str, str] = {}
    embedding_cache: dict[str, str] = {}

    for document in documents:
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
    for start in range(0, len(pending_items), batch_size):
        batch = pending_items[start : start + batch_size]
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
