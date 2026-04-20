from __future__ import annotations

from dory_core.types import MemoryWriteReq, SearchReq, WakeReq


def test_search_req_defaults() -> None:
    req = SearchReq(query="graphql")

    assert req.k == 10
    assert req.mode == "hybrid"


def test_search_req_accepts_common_mode_aliases() -> None:
    assert SearchReq(query="graphql", mode="text").mode == "bm25"  # type: ignore[arg-type]
    assert SearchReq(query="graphql", mode="keyword").mode == "bm25"  # type: ignore[arg-type]
    assert SearchReq(query="graphql", mode="lexical").mode == "bm25"  # type: ignore[arg-type]
    assert SearchReq(query="graphql", mode="semantic").mode == "vector"  # type: ignore[arg-type]


def test_memory_write_req_accepts_legacy_action_aliases() -> None:
    add_req = MemoryWriteReq(action="add", kind="note", subject="dory", content="note")  # type: ignore[arg-type]
    remove_req = MemoryWriteReq(action="remove", kind="note", subject="dory", content="note")  # type: ignore[arg-type]

    assert add_req.action == "write"
    assert remove_req.action == "forget"


def test_wake_req_budget_cap() -> None:
    req = WakeReq(budget_tokens=9_999, agent="claude-code")

    assert req.budget_tokens == 1_500
