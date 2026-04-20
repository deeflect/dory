from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.index.reindex import reindex_corpus
from dory_http.app import build_app
from dory_http.auth import issue_token


def test_http_requires_bearer_token_when_auth_file_present(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    token = issue_token("codex", auth_tokens_path)
    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))

    unauthorized = client.get("/v1/status")
    invalid = client.get("/v1/status", headers={"Authorization": "Bearer nope"})
    authorized = client.get("/v1/status", headers={"Authorization": f"Bearer {token}"})

    assert unauthorized.status_code == 401
    assert invalid.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["files_indexed"] == 7


def test_http_requires_bearer_token_when_auth_file_is_missing_or_empty(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)

    missing_auth_tokens_path = tmp_path / ".dory" / "missing-auth-tokens.json"
    missing_client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=missing_auth_tokens_path))
    missing_response = missing_client.get("/v1/status")

    empty_auth_tokens_path = tmp_path / ".dory" / "empty-auth-tokens.json"
    empty_auth_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    empty_auth_tokens_path.write_text("\n", encoding="utf-8")
    empty_client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=empty_auth_tokens_path))
    empty_response = empty_client.get("/v1/status")

    assert missing_response.status_code == 401
    assert empty_response.status_code == 401


def test_http_reports_invalid_auth_token_configuration(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    auth_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    auth_tokens_path.write_text("{not-json", encoding="utf-8")

    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))
    response = client.get("/v1/status")

    assert response.status_code == 503
    assert "invalid auth token configuration" in response.json()["detail"]
