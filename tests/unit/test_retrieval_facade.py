from __future__ import annotations

from dataclasses import dataclass, field

from dory_core.kernel_retrieval import KernelRetrievalEngine
from dory_core.retrieval import RetrievalFacade
from dory_core.retrieval_planner import DurableSearch, TypedRetrievalPlan
from dory_core.types import SearchReq, SearchResp, SearchResult


@dataclass(slots=True)
class _SearchBackend:
    requests: list[SearchReq] = field(default_factory=list)

    def search(self, req: SearchReq) -> SearchResp:
        self.requests.append(req)
        return SearchResp(
            query=req.query,
            count=1,
            results=[SearchResult(path="core/test.md", lines="1-1", score=1.0, snippet="hit")],
            took_ms=1,
        )


def test_retrieval_facade_delegates_search_unchanged() -> None:
    backend = _SearchBackend()
    facade = RetrievalFacade(
        search_backend=backend,
        kernel_engine=KernelRetrievalEngine(search_engine=backend),
    )
    req = SearchReq(query="alpha", mode="bm25")

    response = facade.search(req)

    assert backend.requests == [req]
    assert response.results[0].path == "core/test.md"


def test_retrieval_facade_executes_and_flattens_typed_plan() -> None:
    backend = _SearchBackend()
    facade = RetrievalFacade(
        search_backend=backend,
        kernel_engine=KernelRetrievalEngine(search_engine=backend),
    )
    plan = TypedRetrievalPlan(attempts=(DurableSearch(query="alpha"),))

    results = facade.execute_typed_plan(plan)
    flat = facade.flatten_typed_plan(plan)

    assert results[0].kind == "durable_search"
    assert flat[0].path == "core/test.md"
