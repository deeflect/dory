from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_http_digest_returns_latest_daily_digest(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    digest = corpus_root / "digests" / "daily" / "2026-05-07.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "---\ntitle: Daily Digest\ntype: digest-daily\n---\n\nShipped a direct digest lookup tool.\n",
        encoding="utf-8",
    )
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post("/v1/digest", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["kind"] == "daily"
    assert payload["period"] == "2026-05-07"
    assert payload["path"] == "digests/daily/2026-05-07.md"
    assert "direct digest lookup" in payload["content"]
    assert "hash" not in payload
    assert "frontmatter" not in payload


def test_http_digest_debug_includes_hash_and_frontmatter(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    digest = corpus_root / "digests" / "weekly" / "2026-W18.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "---\ntitle: Weekly Digest\ntype: digest-weekly\n---\n\nWeekly recap.\n",
        encoding="utf-8",
    )
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post("/v1/digest", json={"kind": "weekly", "debug": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "digests/weekly/2026-W18.md"
    assert payload["frontmatter"]["type"] == "digest-weekly"
    assert payload["hash"].startswith("sha256:")


def test_http_digest_rejects_bad_selector(tmp_path: Path) -> None:
    client = TestClient(build_app(tmp_path / "corpus", tmp_path / "index"))

    response = client.post("/v1/digest", json={"date": "last-week"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_digest_selector"
