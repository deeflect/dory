from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_session_ingest_upserts_cleaned_log(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/session-ingest",
        json={
            "path": "logs/sessions/claude/macbook/2026-04-12-s1.md",
            "content": "We decided Rooster is the focus this week.",
            "agent": "claude",
            "device": "macbook",
            "session_id": "s1",
            "status": "active",
            "captured_from": "claude-local-log",
            "updated": "2026-04-12",
        },
    )

    assert response.status_code == 200
    assert response.json()["stored"] is True
    assert (corpus_root / "logs/sessions/claude/macbook/2026-04-12-s1.md").exists()
    assert (index_root / "session_plane.db").exists()


def test_session_ingest_does_not_trigger_full_reindex(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/session-ingest",
        json={
            "path": "logs/sessions/codex/mac/2026-04-12-s2.md",
            "content": "Changed project direction toward Rooster registry MVP.",
            "agent": "codex",
            "device": "mac",
            "session_id": "s2",
            "status": "active",
            "captured_from": "codex-spool",
            "updated": "2026-04-12",
        },
    )

    assert response.status_code == 200
    assert response.json()["reindexed"] is False


def test_session_ingest_can_be_recalled_through_http_search(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    client = TestClient(build_app(corpus_root, index_root))

    ingest = client.post(
        "/v1/session-ingest",
        json={
            "path": "logs/sessions/claude/macbook/2026-04-12-s3.md",
            "content": "We decided Rooster is the active focus and cleaned up SOUL.",
            "agent": "claude",
            "device": "macbook",
            "session_id": "s3",
            "status": "active",
            "captured_from": "claude-local-log",
            "updated": "2026-04-12T10:15:00Z",
        },
    )
    assert ingest.status_code == 200

    recall = client.post(
        "/v1/search",
        json={
            "query": "cleaned up SOUL",
            "k": 5,
            "mode": "recall",
        },
    )

    assert recall.status_code == 200
    payload = recall.json()
    assert payload["count"] >= 1
    assert payload["results"][0]["path"] == "logs/sessions/claude/macbook/2026-04-12-s3.md"


def test_session_ingest_accepts_relative_runtime_roots(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    corpus_root = Path("corpus")
    index_root = Path("index")
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/session-ingest",
        json={
            "path": "logs/sessions/openclaw/macbook/2026-04-12-s4.md",
            "content": "OpenClaw session shipped with relative roots.",
            "agent": "openclaw",
            "device": "macbook",
            "session_id": "s4",
            "status": "active",
            "captured_from": "openclaw-local-log",
            "updated": "2026-04-12T12:00:00Z",
        },
    )

    assert response.status_code == 200
    assert (workspace / "corpus" / "logs/sessions/openclaw/macbook/2026-04-12-s4.md").exists()
