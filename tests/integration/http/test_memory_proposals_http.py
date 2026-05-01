from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_http_memory_proposal_lifecycle(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal state.\n",
        encoding="utf-8",
    )
    client = TestClient(build_app(corpus_root, index_root, embedder=fake_embedder))

    created = client.post(
        "/v1/memory-proposals",
        json={
            "proposal_id": "http-proposal",
            "action": "replace",
            "kind": "state",
            "subject": "active",
            "content": "## Current State\n\nHTTP proposal state.",
            "scope": "core",
            "agent": "codex",
            "origin_surface": "http-test",
        },
    )
    listed = client.get("/v1/memory-proposals")
    fetched = client.get("/v1/memory-proposals/http-proposal")
    applied = client.post(
        "/v1/memory-proposals/apply",
        json={"proposal_id": "http-proposal", "agent": "reviewer"},
    )

    assert created.status_code == 200, created.text
    created_payload = created.json()
    assert created_payload["proposal_id"] == "http-proposal"
    assert created_payload["proposal"]["actions"][0]["dry_run"]["target_path"] == "core/active.md"
    assert listed.status_code == 200, listed.text
    assert listed.json()["proposals"] == ["http-proposal"]
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["agent"] == "codex"
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == ["core/active.md"]
    assert "HTTP proposal state." in (corpus_root / "core" / "active.md").read_text(encoding="utf-8")


def test_http_memory_proposal_reject_path(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal state.\n",
        encoding="utf-8",
    )
    client = TestClient(build_app(corpus_root, index_root, embedder=fake_embedder))

    created = client.post(
        "/v1/memory-proposals",
        json={
            "proposal_id": "http-reject",
            "action": "write",
            "kind": "note",
            "subject": "active",
            "content": "Reject this.",
            "scope": "core",
        },
    )
    rejected = client.post("/v1/memory-proposals/http-reject/reject", params={"reason": "not needed"})

    assert created.status_code == 200, created.text
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["path"] == "inbox/rejected/http-reject.json"
    assert not (corpus_root / "inbox" / "proposed" / "http-reject.json").exists()
