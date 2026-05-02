from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app


def test_profiles_http_route_lists_custom_profile_details(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    (corpus_root / "profiles.yaml").write_text(
        """
profiles:
  brand:
    wake:
      sections:
        - profiles/brand/default.md
    retrieval:
      allow:
        - profiles/brand/**
      sessions: never
""".strip(),
        encoding="utf-8",
    )
    client = TestClient(build_app(corpus_root, index_root))

    response = client.get("/v1/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    brand = next(profile for profile in payload["profiles"] if profile["name"] == "brand")
    assert brand["source"] == "custom"
    assert brand["wake"]["sections"] == ["profiles/brand/default.md"]
    assert brand["retrieval"]["allow"] == ["profiles/brand/**"]
    assert brand["retrieval"]["sessions"] == "never"
