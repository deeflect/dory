from __future__ import annotations

from dory_core.research import ResearchEngine
from dory_core.types import ArtifactReq, ResearchReq, SearchResult


class _StubSearchEngine:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.requests = []

    def search(self, req):  # pragma: no cover - test stub
        self.requests.append(req)
        return type("Resp", (), {"results": self.results})()


def test_research_engine_builds_report_artifact_request() -> None:
    engine = ResearchEngine(
        search_engine=_StubSearchEngine(
            [
                SearchResult(
                    path="core/active.md",
                    lines="1:4",
                    score=0.95,
                    snippet="Rooster is the active focus this week.",
                    frontmatter={},
                ),
                SearchResult(
                    path="wiki/projects/rooster.md",
                    lines="1:10",
                    score=0.91,
                    snippet="Current compiled view of the Rooster project.",
                    frontmatter={},
                ),
            ]
        )
    )

    resp = engine.research(
        "What are we working on right now?",
        kind="report",
        corpus="all",
    )

    assert isinstance(resp.artifact, ArtifactReq)
    assert resp.artifact.kind == "report"
    assert "## Answer" in resp.artifact.body
    assert "## Evidence" in resp.artifact.body
    assert "Rooster" in resp.artifact.body
    assert resp.sources[0] == "core/active.md"
    assert resp.artifact.sources == ["core/active.md", "wiki/projects/rooster.md"]
    assert engine.search_engine.requests[0].include_content is False
    assert engine.search_engine.requests[0].rerank == "true"


def test_research_engine_from_req_uses_request_fields() -> None:
    engine = ResearchEngine(
        search_engine=_StubSearchEngine(
            [
                SearchResult(
                    path="people/anna.md",
                    lines="1:4",
                    score=0.88,
                    snippet="Anna is mentioned here.",
                    frontmatter={},
                )
            ]
        )
    )

    resp = engine.research_from_req(ResearchReq(question="Who is Anna?", kind="briefing", corpus="durable", limit=3))

    assert resp.artifact.kind == "briefing"
    assert resp.sources == ["people/anna.md"]
    assert "## Question" in resp.artifact.body
    assert "Anna" in resp.artifact.body
