from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.index.reindex import reindex_corpus
from dory_http.app import build_app


def test_stream_route_emits_status_and_done(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    client = TestClient(build_app(corpus_root, index_root))

    response = client.get("/v1/stream", params={"reindex": "true"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in response.text
    assert "event: reindex" in response.text
    assert "event: done" in response.text
