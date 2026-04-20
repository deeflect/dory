from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from dory_core.config import DorySettings
from dory_core.llm.openrouter import (
    OpenRouterClient,
    OpenRouterProviderError,
    build_openrouter_client,
)


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    chunk_id: str
    path: str
    title: str
    snippet: str
    frontmatter_hints: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankResult:
    ordered_chunk_ids: tuple[str, ...]
    scores: dict[str, float]


class LLMReranker(Protocol):
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> RerankResult | None: ...


_RERANK_SYSTEM_PROMPT = (
    "You reorder retrieval candidates for a personal memory system. "
    "For each candidate return a relevance score from 0.0 to 1.0 based on:\n"
    "- topical match to the query's actual intent\n"
    "- freshness: prefer documents that describe current truth over superseded ones\n"
    "- canonicality: canonical documents outrank extracted or session variants\n"
    "- specificity: precise matches outrank broad mentions\n"
    "Return every candidate you received, ordered best-first by score."
)

_RERANK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "chunk_id": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["chunk_id", "score"],
            },
        }
    },
    "required": ["ranking"],
}

_MAX_SNIPPET_CHARS = 500


@dataclass(frozen=True, slots=True)
class OpenRouterReranker:
    client: OpenRouterClient

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> RerankResult | None:
        if not candidates:
            return RerankResult(ordered_chunk_ids=(), scores={})
        user_prompt = _build_user_prompt(query, candidates)
        try:
            payload = self.client.generate_json(
                system_prompt=_RERANK_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_name="dory_rerank",
                schema=_RERANK_SCHEMA,
            )
        except OpenRouterProviderError:
            return None
        return _parse_rerank_payload(payload, candidates)


def _build_user_prompt(query: str, candidates: Sequence[RerankCandidate]) -> str:
    lines = [f"Query: {query}", "", "Candidates:"]
    for candidate in candidates:
        hint_text = ", ".join(f"{key}={value}" for key, value in candidate.frontmatter_hints.items() if value)
        header = f"- id={candidate.chunk_id} | path={candidate.path} | title={candidate.title or '(untitled)'}"
        if hint_text:
            header += f" | {hint_text}"
        lines.append(header)
        snippet = candidate.snippet.strip()
        if snippet:
            truncated = snippet[:_MAX_SNIPPET_CHARS]
            lines.append(f"  snippet: {truncated}")
    return "\n".join(lines)


def _parse_rerank_payload(
    payload: Any,
    candidates: Sequence[RerankCandidate],
) -> RerankResult | None:
    if not isinstance(payload, dict):
        return None
    ranking = payload.get("ranking")
    if not isinstance(ranking, list):
        return None
    valid_ids = {candidate.chunk_id for candidate in candidates}
    ordered: list[str] = []
    scores: dict[str, float] = {}
    seen: set[str] = set()
    for item in ranking:
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id not in valid_ids or chunk_id in seen:
            continue
        try:
            score_value = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        ordered.append(chunk_id)
        scores[chunk_id] = score_value
        seen.add(chunk_id)
    if not ordered:
        return None
    for candidate in candidates:
        if candidate.chunk_id not in seen:
            ordered.append(candidate.chunk_id)
            scores[candidate.chunk_id] = 0.0
            seen.add(candidate.chunk_id)
    return RerankResult(ordered_chunk_ids=tuple(ordered), scores=scores)


def build_reranker(settings: DorySettings | None = None) -> OpenRouterReranker | None:
    resolved_settings = settings or DorySettings()
    if not resolved_settings.query_reranker_enabled:
        return None
    client = build_openrouter_client(resolved_settings, purpose="query")
    if client is None:
        return None
    return OpenRouterReranker(client=client)
