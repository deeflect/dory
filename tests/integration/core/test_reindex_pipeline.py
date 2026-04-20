from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.index.sqlite_store import SqliteStore
from dory_core.index.sqlite_vector_store import SqliteVectorStore


def test_reindex_indexes_fixture_corpus(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder: object,
) -> None:
    result = reindex_corpus(sample_corpus_root, tmp_path, fake_embedder)

    sqlite_store = SqliteStore(tmp_path / "dory.db")
    vector_store = SqliteVectorStore(tmp_path / "dory.db", dimension=768)

    assert result.files_indexed == 7
    assert result.chunks_indexed >= 7
    assert result.vectors_indexed == result.chunks_indexed
    assert sqlite_store.count_rows("files") == 7
    assert sqlite_store.count_rows("chunks") == result.chunks_indexed
    assert vector_store.count() == result.chunks_indexed


def test_reindex_reuses_embedding_cache_for_unchanged_chunks(
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    class CountingEmbedder:
        dimension = 4
        model = "gemini-embedding-001"

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return [[float(len(text)), 0.0, 0.0, 0.0] for text in texts]

    first = CountingEmbedder()
    second = CountingEmbedder()

    reindex_corpus(sample_corpus_root, tmp_path, first)
    reindex_corpus(sample_corpus_root, tmp_path, second)

    assert sum(len(batch) for batch in first.calls) >= 7
    assert sum(len(batch) for batch in second.calls) == 0

    sqlite_store = SqliteStore(tmp_path / "dory.db")
    assert sqlite_store.count_rows("embedding_cache_meta") >= 1


def test_reindex_invalidates_embedding_cache_when_model_changes(
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    class CountingEmbedder:
        dimension = 4

        def __init__(self, model: str) -> None:
            self.model = model
            self.calls: list[list[str]] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return [[float(len(text)), 0.0, 0.0, 0.0] for text in texts]

    first = CountingEmbedder("gemini-embedding-001")
    second = CountingEmbedder("gemini-embedding-002")

    result = reindex_corpus(sample_corpus_root, tmp_path, first)
    reindex_corpus(sample_corpus_root, tmp_path, second)

    assert sum(len(batch) for batch in first.calls) == result.chunks_indexed
    assert sum(len(batch) for batch in second.calls) == result.chunks_indexed
