from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.frontmatter import load_markdown_document
from dory_core.errors import DoryValidationError
from dory_core.index.reindex import reindex_corpus
from dory_http.app import build_app
from dory_http.auth import issue_token


def _seed_semantic_write_corpus(root: Path) -> None:
    (root / "people").mkdir(parents=True)
    (root / "projects" / "rooster").mkdir(parents=True)
    (root / "core").mkdir(parents=True)

    (root / "people" / "alex-example.md").write_text(
        "---\ntitle: Alex Example\naliases:\n  - anna\n---\n# Anna\n\n## Summary\n\nInitial summary.\n",
        encoding="utf-8",
    )
    (root / "projects" / "rooster" / "state.md").write_text(
        "---\ntitle: Rooster\n---\n# Rooster\n\n## Current State\n\nRooster is active.\n",
        encoding="utf-8",
    )
    (root / "core" / "user.md").write_text(
        "---\ntitle: User\n---\n# User\n",
        encoding="utf-8",
    )


def test_http_memory_write_handles_write_replace_and_forget(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _seed_semantic_write_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)

    client = TestClient(build_app(corpus_root, index_root))

    write = client.post(
        "/v1/memory-write",
        json={
            "action": "write",
            "kind": "fact",
            "subject": "anna",
            "content": "Prefers async work.",
            "scope": "person",
            "allow_canonical": True,
        },
    )
    assert write.status_code == 200, write.text
    write_payload = write.json()
    assert write_payload["resolved"] is True
    assert write_payload["result"] == "written"
    assert write_payload["target_path"] == "people/alex-example.md"
    assert write_payload["indexed"] is True
    assert "Prefers async work." in (corpus_root / "people" / "alex-example.md").read_text(encoding="utf-8")

    replace = client.post(
        "/v1/memory-write",
        json={
            "action": "replace",
            "kind": "state",
            "subject": "rooster",
            "content": "# Rooster\n\n## Current State\n\nRooster is paused.",
            "scope": "project",
            "allow_canonical": True,
        },
    )
    assert replace.status_code == 200, replace.text
    replace_payload = replace.json()
    assert replace_payload["resolved"] is True
    assert replace_payload["result"] == "replaced"
    assert replace_payload["target_path"] == "projects/rooster/state.md"
    project_document = load_markdown_document(
        (corpus_root / "projects" / "rooster" / "state.md").read_text(encoding="utf-8")
    )
    assert "Rooster is paused." in project_document.body
    assert "Rooster is active." not in project_document.body

    forget = client.post(
        "/v1/memory-write",
        json={
            "action": "forget",
            "kind": "note",
            "subject": "anna",
            "content": "Old preference note should be removed.",
            "scope": "person",
            "reason": "no longer valid",
            "allow_canonical": True,
        },
    )
    assert forget.status_code == 200, forget.text
    forget_payload = forget.json()
    assert forget_payload["resolved"] is True
    assert forget_payload["result"] == "forgotten"
    assert forget_payload["target_path"] == "people/alex-example.md"

    semantic_artifacts = sorted((corpus_root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 3
    artifact_frontmatters = [
        load_markdown_document(path.read_text(encoding="utf-8")).frontmatter for path in semantic_artifacts
    ]
    assert {frontmatter["action"] for frontmatter in artifact_frontmatters} == {
        "write",
        "replace",
        "forget",
    }
    assert all(frontmatter["source_kind"] == "semantic" for frontmatter in artifact_frontmatters)

    person_document = load_markdown_document((corpus_root / "people" / "alex-example.md").read_text(encoding="utf-8"))
    assert person_document.frontmatter["superseded_by"] == "alex-example.tombstone.md"
    tombstone_path = corpus_root / "people" / "alex-example.tombstone.md"
    assert tombstone_path.exists()
    assert "no longer valid" in tombstone_path.read_text(encoding="utf-8")


def test_http_memory_write_quarantines_unresolved_subjects(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _seed_semantic_write_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)

    client = TestClient(build_app(corpus_root, index_root))
    preview_response = client.post(
        "/v1/memory-write",
        json={
            "action": "write",
            "kind": "fact",
            "subject": "completely unrelated subject",
            "content": "This should not write.",
            "soft": True,
            "dry_run": True,
        },
    )
    response = client.post(
        "/v1/memory-write",
        json={
            "action": "write",
            "kind": "fact",
            "subject": "completely unrelated subject",
            "content": "This should not write.",
            "soft": True,
        },
    )

    assert preview_response.status_code == 200, preview_response.text
    preview_payload = preview_response.json()
    assert response.status_code == 200, response.text
    payload = response.json()
    assert preview_payload["result"] == "preview"
    assert preview_payload["target_path"] == payload["target_path"]
    assert payload["resolved"] is False
    assert payload["result"] == "quarantined"
    assert payload["quarantined"] is True
    assert payload["target_path"] is not None
    assert payload["message"] is not None
    assert "could not resolve semantic subject" in payload["message"]
    quarantine_path = corpus_root / payload["target_path"]
    assert quarantine_path.exists()
    rendered = quarantine_path.read_text(encoding="utf-8")
    assert "This should not write." in rendered
    assert "quarantine_reason:" in rendered
    assert "could not resolve semantic subject" in rendered


def test_http_memory_write_rejects_canonical_without_allow_flag(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _seed_semantic_write_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)

    client = TestClient(build_app(corpus_root, index_root))
    response = client.post(
        "/v1/memory-write",
        json={
            "action": "write",
            "kind": "fact",
            "subject": "anna",
            "content": "Prefers async work.",
            "scope": "person",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resolved"] is True
    assert payload["result"] == "rejected"
    assert payload["target_path"] == "people/alex-example.md"
    assert "allow_canonical=true" in payload["message"]
    assert "Prefers async work." not in (corpus_root / "people" / "alex-example.md").read_text(encoding="utf-8")


def test_http_memory_write_respects_bearer_auth(
    tmp_path: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"
    _seed_semantic_write_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)
    token = issue_token("codex", auth_tokens_path)

    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))
    payload = {
        "action": "write",
        "kind": "state",
        "subject": "user",
        "content": "Memory write is authorized.",
        "scope": "core",
        "allow_canonical": True,
    }

    unauthorized = client.post("/v1/memory-write", json=payload)
    invalid = client.post(
        "/v1/memory-write",
        json=payload,
        headers={"Authorization": "Bearer nope"},
    )
    authorized = client.post(
        "/v1/memory-write",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert unauthorized.status_code == 401
    assert invalid.status_code == 401
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["result"] == "written"


def test_http_memory_write_returns_400_for_validation_errors(
    tmp_path: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _seed_semantic_write_corpus(corpus_root)
    reindex_corpus(corpus_root, index_root, fake_embedder)

    class _BrokenEngine:
        def write(self, req):  # pragma: no cover - forced failure path
            raise DoryValidationError("semantic write failed")

    monkeypatch.setattr(
        "dory_http.app._build_semantic_write_engine",
        lambda runtime: _BrokenEngine(),
    )
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/memory-write",
        json={
            "action": "write",
            "kind": "fact",
            "subject": "anna",
            "content": "Prefers async work.",
            "scope": "person",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "semantic write failed"
