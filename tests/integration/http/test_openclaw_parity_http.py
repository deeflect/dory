from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.index.reindex import reindex_corpus
from dory_http.app import build_app


def _seed_public_artifacts(root: Path) -> None:
    (root / "core").mkdir(parents=True)
    (root / "references" / "reports" / "migrations").mkdir(parents=True)
    (root / "wiki" / "projects").mkdir(parents=True)

    (root / "core" / "user.md").write_text(
        "---\ntitle: User\nagent_ids: [openclaw]\n---\n# User\n",
        encoding="utf-8",
    )
    (root / "references" / "reports" / "migrations" / "2026-04-13-rooster.md").write_text(
        "---\ntitle: Rooster Migration\nagent: openclaw\n---\n# Rooster Migration\n",
        encoding="utf-8",
    )
    (root / "wiki" / "projects" / "rooster.md").write_text(
        "---\ntitle: Rooster\n---\n# Rooster\n",
        encoding="utf-8",
    )


def test_http_recall_event_and_public_artifacts_and_status(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    _seed_public_artifacts(corpus_root)
    client = TestClient(build_app(corpus_root, index_root))

    recall = client.post(
        "/v1/recall-event",
        json={
            "agent": "openclaw",
            "session_key": "sess-1",
            "query": "who is anna",
            "result_paths": ["people/anna.md", "core/user.md"],
            "selected_path": "people/anna.md",
            "corpus": "memory",
            "source": "openclaw-recall",
        },
    )
    artifacts = client.get("/v1/public-artifacts")
    status = client.get("/v1/status")

    assert recall.status_code == 200, recall.text
    assert recall.json()["stored"] is True
    assert recall.json()["selected_path"] == "people/anna.md"

    assert artifacts.status_code == 200, artifacts.text
    artifact_paths = [artifact["relative_path"] for artifact in artifacts.json()["artifacts"]]
    assert artifact_paths == [
        "core/user.md",
        "references/reports/migrations/2026-04-13-rooster.md",
        "wiki/projects/rooster.md",
    ]

    assert status.status_code == 200, status.text
    payload = status.json()
    assert payload["openclaw"]["recall_tracking_enabled"] is True
    assert payload["openclaw"]["artifact_listing_enabled"] is True
    assert payload["openclaw"]["recent_recall_count"] == 1
    assert payload["openclaw"]["last_recall_selected_path"] == "people/anna.md"


def test_http_search_respects_min_score_for_openclaw_clients(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "people").mkdir(parents=True)
    (corpus_root / "people" / "anna.md").write_text(
        "---\n"
        "title: Anna\n"
        "type: person\n"
        "status: active\n"
        "canonical: true\n"
        "---\n"
        "# Anna\n\n"
        "## Summary\n\n"
        "Anna prefers async work.\n",
        encoding="utf-8",
    )
    reindex_corpus(corpus_root, index_root, fake_embedder)
    client = TestClient(build_app(corpus_root, index_root))

    baseline = client.post(
        "/v1/search",
        json={"query": "Anna prefers async work", "mode": "hybrid", "k": 5},
    )
    thresholded = client.post(
        "/v1/search",
        json={"query": "Anna prefers async work", "mode": "hybrid", "k": 5, "min_score": 100.0},
    )

    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["count"] >= 1
    assert thresholded.status_code == 200, thresholded.text
    assert thresholded.json()["count"] == 0
    assert thresholded.json()["results"] == []
