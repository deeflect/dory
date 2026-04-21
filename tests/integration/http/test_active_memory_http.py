from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.embedding import EmbeddingProviderError
from dory_core.types import ActiveMemoryResp
from dory_http.app import build_app


class _StubActiveMemoryEngine:
    def __init__(self, response: ActiveMemoryResp) -> None:
        self.response = response
        self.requests: list[object] = []

    def build(self, req):  # pragma: no cover - simple test stub
        self.requests.append(req)
        return self.response


def test_active_memory_http_endpoint_returns_memory_block(tmp_path: Path, monkeypatch) -> None:
    response = ActiveMemoryResp(
        kind="memory",
        block="## Active memory\n- Rooster is the focus.",
        summary="Rooster is the focus.",
        confidence="medium",
        sources=["core/active.md"],
    )
    stub = _StubActiveMemoryEngine(response)
    monkeypatch.setattr("dory_http.app._build_active_memory_engine", lambda runtime: stub)

    client = TestClient(build_app(tmp_path / "corpus", tmp_path / "index"))
    result = client.post(
        "/v1/active-memory",
        json={
            "prompt": "what are we working on today",
            "agent": "claude",
            "profile": "general",
            "cwd": str(tmp_path),
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["kind"] == "memory"
    assert payload["summary"] == "Rooster is the focus."
    assert "took_ms" in payload
    assert stub.requests
    assert stub.requests[0].profile == "general"


def test_active_memory_http_returns_503_for_embedding_provider_errors(tmp_path: Path, monkeypatch) -> None:
    class _BrokenActiveMemoryEngine:
        def build(self, req):
            raise EmbeddingProviderError("embedding backend unavailable")

    monkeypatch.setattr("dory_http.app._build_active_memory_engine", lambda runtime: _BrokenActiveMemoryEngine())

    client = TestClient(build_app(tmp_path / "corpus", tmp_path / "index"))
    result = client.post(
        "/v1/active-memory",
        json={
            "prompt": "what are we working on today",
            "agent": "claude",
        },
    )

    assert result.status_code == 503
    assert result.json()["detail"] == "embedding backend unavailable"
