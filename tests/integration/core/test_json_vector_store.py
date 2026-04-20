from __future__ import annotations

from pathlib import Path

from dory_core.index.json_vector_store import JsonVectorStore, VectorRecord


class FakeEmbedder:
    dimension = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i) for i in range(self.dimension)] for _ in texts]


def test_json_vector_store_persists_rows(tmp_path: Path) -> None:
    store = JsonVectorStore(tmp_path / "lance", dimension=768)
    fake_embedder = FakeEmbedder()
    vector = fake_embedder.embed(["hello"])[0]
    record = VectorRecord(
        chunk_id="chunk-1",
        content_hash="sha256:abc",
        vector=vector,
    )

    written = store.upsert([record])

    assert written == 1
    assert store.count() == 1
    assert store.get("chunk-1") == record
    assert (tmp_path / "lance" / "chunks_vec.json").exists()

    reloaded = JsonVectorStore(tmp_path / "lance", dimension=768)
    assert reloaded.get("chunk-1") == record
