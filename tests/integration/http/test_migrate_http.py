from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_migrate_http_bootstraps_canonical_pages(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    legacy_root = Path("tests/fixtures/legacy_clawd_brain")
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/migrate",
        json={"legacy_root": str(legacy_root), "use_llm": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["staged_count"] >= 1
    assert payload["written_count"] >= 1
    assert payload["canonical_created_count"] == 0
    assert payload["quarantined_count"] == 0
    assert payload["stats"]["fallback_classified_count"] >= 1
    assert payload["stats"]["duration_ms"] >= 0
    assert (corpus_root / "sources" / "imported" / "user.md").exists()
    assert not (corpus_root / "projects" / "rooster" / "state.md").exists()
    assert (corpus_root / "references" / "reports" / "migrations").exists()
