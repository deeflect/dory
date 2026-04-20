from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.embedding import EmbeddingProviderError
from dory_core.types import ArtifactReq, ResearchResp
from dory_http.app import build_app


class _FakeSearchEngine:
    def search(self, req):  # pragma: no cover - test stub
        from dory_core.types import SearchResult

        return type(
            "Resp",
            (),
            {
                "results": [
                    SearchResult(
                        path="core/active.md",
                        lines="1:1",
                        score=0.99,
                        snippet="Rooster is the active focus this week.",
                        frontmatter={},
                    )
                ]
            },
        )()


def test_http_research_writes_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    class _FakeEmbedder:
        dimension = 1

        def embed(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(
        "dory_http.app.build_runtime_embedder",
        _FakeEmbedder,
    )
    monkeypatch.setattr(
        "dory_http.app.ResearchEngine",
        lambda search_engine: type(
            "FakeResearchEngine",
            (),
            {
                "research_from_req": staticmethod(
                    lambda req: ResearchResp(
                        artifact=ArtifactReq(
                            kind=req.kind,
                            title=req.question.rstrip("?"),
                            question=req.question,
                            body="Rooster is the active focus this week.",
                            sources=["core/active.md"],
                        ),
                        sources=["core/active.md"],
                    )
                )
            },
        )(),
    )

    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/research",
        json={"question": "What are we working on right now?", "kind": "report", "corpus": "all"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["artifact"]["path"].startswith("references/reports/")
    assert (corpus_root / payload["artifact"]["path"]).exists()
    assert "core/active.md" in payload["research"]["sources"]


def test_http_research_returns_503_for_embedding_provider_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _BrokenResearchEngine:
        def research_from_req(self, req):
            raise EmbeddingProviderError("embedding backend unavailable")

    monkeypatch.setattr("dory_http.app.ResearchEngine", lambda search_engine: _BrokenResearchEngine())

    client = TestClient(build_app(tmp_path / "corpus", tmp_path / "index"))
    response = client.post(
        "/v1/research",
        json={"question": "What are we working on right now?", "kind": "report", "corpus": "all"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding backend unavailable"
