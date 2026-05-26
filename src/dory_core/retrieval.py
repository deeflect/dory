from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dory_core.kernel_retrieval import KernelAttemptResult, KernelRetrievalEngine, flatten_kernel_results
from dory_core.retrieval_planner import TypedRetrievalPlan
from dory_core.types import SearchReq, SearchResp


class SearchBackend(Protocol):
    def search(self, req: SearchReq) -> SearchResp: ...


@dataclass(frozen=True, slots=True)
class RetrievalFacade:
    """Runtime boundary for retrieval operations.

    The facade currently preserves existing ``SearchEngine.search`` behavior
    exactly. It gives runtime/adapters one retrieval boundary while typed
    kernel attempts mature behind the same surface.
    """

    search_backend: SearchBackend
    kernel_engine: KernelRetrievalEngine

    def search(self, req: SearchReq) -> SearchResp:
        return self.search_backend.search(req)

    def execute_typed_plan(
        self,
        plan: TypedRetrievalPlan,
        *,
        max_attempts: int = 8,
        source_policy: object | None = None,
    ) -> list[KernelAttemptResult]:
        return self.kernel_engine.execute(
            plan,
            max_attempts=max_attempts,
            source_policy=source_policy,
        )

    def flatten_typed_plan(
        self,
        plan: TypedRetrievalPlan,
        *,
        max_attempts: int = 8,
        source_policy: object | None = None,
    ) -> list[object]:
        results = self.execute_typed_plan(
            plan,
            max_attempts=max_attempts,
            source_policy=source_policy,
        )
        return flatten_kernel_results(results, source_policy=source_policy)
