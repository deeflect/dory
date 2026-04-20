from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_http_get_returns_frontmatter_and_hash(tmp_path: Path, sample_corpus_root: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    client = TestClient(build_app(corpus_root, index_root))
    response = client.get("/v1/get", params={"path": "core/user.md", "from": 1, "lines": 8})

    assert response.status_code == 200
    payload = response.json()
    expected_text = (corpus_root / "core/user.md").read_text(encoding="utf-8")
    assert payload["frontmatter"]["title"] == "User"
    assert payload["hash"] == f"sha256:{sha256(expected_text.encode('utf-8')).hexdigest()}"


def test_http_get_rejects_non_positive_line_limit(tmp_path: Path, sample_corpus_root: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    client = TestClient(build_app(corpus_root, index_root))
    response = client.get("/v1/get", params={"path": "core/user.md", "from": 1, "lines": -1})

    assert response.status_code == 400
    assert response.json()["detail"] == "'lines' must be >= 1"
