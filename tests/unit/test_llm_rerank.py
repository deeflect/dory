from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from dory_core.llm.openrouter import OpenRouterProviderError
from dory_core.llm_rerank import (
    OpenRouterReranker,
    RerankCandidate,
    _build_user_prompt,
    _parse_rerank_payload,
)


@dataclass
class _FakeClient:
    payload: Any = None
    raise_error: bool = False
    captured_system: str | None = None
    captured_user: str | None = None
    captured_schema_name: str | None = None

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict,
    ) -> Any:
        self.captured_system = system_prompt
        self.captured_user = user_prompt
        self.captured_schema_name = schema_name
        if self.raise_error:
            raise OpenRouterProviderError("boom")
        return self.payload


def _candidate(chunk_id: str, *, path: str = "", title: str = "", snippet: str = "") -> RerankCandidate:
    return RerankCandidate(
        chunk_id=chunk_id,
        path=path or f"docs/{chunk_id}.md",
        title=title or chunk_id.title(),
        snippet=snippet,
        frontmatter_hints={},
    )


def test_openrouter_reranker_returns_ordered_result() -> None:
    client = _FakeClient(
        payload={
            "ranking": [
                {"chunk_id": "b", "score": 0.9},
                {"chunk_id": "a", "score": 0.4},
            ]
        }
    )
    reranker = OpenRouterReranker(client=client)  # type: ignore[arg-type]

    result = reranker.rerank(query="q", candidates=[_candidate("a"), _candidate("b")])

    assert result is not None
    assert result.ordered_chunk_ids == ("b", "a")
    assert result.scores == {"b": 0.9, "a": 0.4}


def test_openrouter_reranker_handles_empty_candidates() -> None:
    client = _FakeClient(payload={"ranking": []})
    reranker = OpenRouterReranker(client=client)  # type: ignore[arg-type]

    result = reranker.rerank(query="q", candidates=[])

    assert result is not None
    assert result.ordered_chunk_ids == ()
    assert result.scores == {}


def test_openrouter_reranker_returns_none_on_provider_error() -> None:
    client = _FakeClient(raise_error=True)
    reranker = OpenRouterReranker(client=client)  # type: ignore[arg-type]

    result = reranker.rerank(query="q", candidates=[_candidate("a")])

    assert result is None


def test_openrouter_reranker_returns_none_on_malformed_payload() -> None:
    client = _FakeClient(payload={"wrong": []})
    reranker = OpenRouterReranker(client=client)  # type: ignore[arg-type]

    result = reranker.rerank(query="q", candidates=[_candidate("a")])

    assert result is None


def test_parse_appends_missing_candidates_at_end() -> None:
    payload = {"ranking": [{"chunk_id": "b", "score": 0.8}]}
    result = _parse_rerank_payload(payload, [_candidate("a"), _candidate("b"), _candidate("c")])

    assert result is not None
    assert result.ordered_chunk_ids == ("b", "a", "c")
    assert result.scores["b"] == pytest.approx(0.8)
    assert result.scores["a"] == 0.0
    assert result.scores["c"] == 0.0


def test_parse_rejects_unknown_chunk_ids() -> None:
    payload = {
        "ranking": [
            {"chunk_id": "nope", "score": 0.9},
            {"chunk_id": "a", "score": 0.5},
        ]
    }
    result = _parse_rerank_payload(payload, [_candidate("a"), _candidate("b")])

    assert result is not None
    assert result.ordered_chunk_ids == ("a", "b")
    assert "nope" not in result.scores


def test_parse_deduplicates_repeated_ids() -> None:
    payload = {
        "ranking": [
            {"chunk_id": "a", "score": 0.9},
            {"chunk_id": "a", "score": 0.1},
            {"chunk_id": "b", "score": 0.4},
        ]
    }
    result = _parse_rerank_payload(payload, [_candidate("a"), _candidate("b")])

    assert result is not None
    assert result.ordered_chunk_ids == ("a", "b")
    assert result.scores["a"] == pytest.approx(0.9)


def test_parse_returns_none_when_no_valid_entries() -> None:
    payload = {"ranking": [{"chunk_id": "ghost", "score": 0.5}]}
    result = _parse_rerank_payload(payload, [_candidate("a")])

    assert result is None


def test_build_user_prompt_includes_hints_and_snippets() -> None:
    candidates = [
        RerankCandidate(
            chunk_id="c1",
            path="core/env.md",
            title="Environment",
            snippet="Current default model is X.",
            frontmatter_hints={"type": "core", "status": "active"},
        ),
    ]
    prompt = _build_user_prompt("what is the default model?", candidates)

    assert "Query: what is the default model?" in prompt
    assert "id=c1" in prompt
    assert "path=core/env.md" in prompt
    assert "title=Environment" in prompt
    assert "type=core" in prompt
    assert "status=active" in prompt
    assert "snippet: Current default model is X." in prompt


def test_build_user_prompt_truncates_long_snippets() -> None:
    long_snippet = "x" * 2000
    candidate = RerankCandidate(
        chunk_id="c1",
        path="docs/a.md",
        title="A",
        snippet=long_snippet,
    )
    prompt = _build_user_prompt("q", [candidate])

    assert "x" * 500 in prompt
    assert "x" * 501 not in prompt
