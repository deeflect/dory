from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

import httpx

from dory_core.config import DorySettings
from dory_core.llm.openai_compatible import (
    _format_error_response,
    _normalize_base_url,
    _should_retry_status,
    _sleep_backoff,
)
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


@dataclass(frozen=True, slots=True)
class OpenAICompatibleReranker:
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float = 30.0
    retries: int = 2

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> RerankResult | None:
        if not candidates:
            return RerankResult(ordered_chunk_ids=(), scores={})

        documents = [_document_text_from_candidate(candidate) for candidate in candidates]
        payload: dict[str, object] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response_payload = self._post_rerank(headers=headers, payload=payload)
        except OpenRouterProviderError:
            return None
        return _parse_openai_compatible_rerank_payload(response_payload, candidates)

    def _post_rerank(self, *, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
        last_error: Exception | None = None
        with httpx.Client(base_url=_normalize_base_url(self.base_url), timeout=self.timeout_seconds) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = client.post("/rerank", headers=headers, json=payload)
                except httpx.HTTPError as err:
                    last_error = err
                    if attempt >= self.retries:
                        raise OpenRouterProviderError(f"OpenAI-compatible rerank request failed: {err}") from err
                    _sleep_backoff(attempt)
                    continue

                if response.status_code < 400:
                    try:
                        response_payload = response.json()
                    except ValueError as err:
                        raise OpenRouterProviderError("OpenAI-compatible rerank endpoint returned invalid JSON.") from err
                    if not isinstance(response_payload, dict):
                        raise OpenRouterProviderError("OpenAI-compatible rerank endpoint returned a non-object payload.")
                    return response_payload
                if not _should_retry_status(response.status_code) or attempt >= self.retries:
                    raise OpenRouterProviderError(_format_error_response(response))
                _sleep_backoff(attempt, retry_after=response.headers.get("Retry-After"))

        if last_error is not None:
            raise OpenRouterProviderError(f"OpenAI-compatible rerank request failed: {last_error}") from last_error
        raise OpenRouterProviderError("OpenAI-compatible rerank request failed: unknown provider error")


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


def _parse_openai_compatible_rerank_payload(
    payload: dict[str, object],
    candidates: Sequence[RerankCandidate],
) -> RerankResult | None:
    results = payload.get("results")
    if not isinstance(results, list):
        return None

    scored: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
            score = float(item.get("relevance_score"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(candidates):
            scored.append((index, score))
    if not scored:
        return None

    seen_indexes: set[int] = set()
    ordered_indexes: list[int] = []
    scores: dict[str, float] = {}
    for index, score in sorted(scored, key=lambda item: (-item[1], item[0])):
        if index in seen_indexes:
            continue
        chunk_id = candidates[index].chunk_id
        ordered_indexes.append(index)
        scores[chunk_id] = score
        seen_indexes.add(index)

    for index, candidate in enumerate(candidates):
        if index in seen_indexes:
            continue
        ordered_indexes.append(index)
        scores[candidate.chunk_id] = 0.0

    return RerankResult(
        ordered_chunk_ids=tuple(candidates[index].chunk_id for index in ordered_indexes),
        scores=scores,
    )


def _document_text_from_candidate(candidate: RerankCandidate) -> str:
    lines = [f"path: {candidate.path}", f"title: {candidate.title or '(untitled)'}"]
    for key, value in candidate.frontmatter_hints.items():
        if value:
            lines.append(f"{key}: {value}")
    snippet = candidate.snippet.strip()
    if snippet:
        lines.append("")
        lines.append(snippet[:_MAX_SNIPPET_CHARS])
    return "\n".join(lines)


def build_reranker(settings: DorySettings | None = None) -> OpenRouterReranker | OpenAICompatibleReranker | None:
    resolved_settings = settings or DorySettings()
    if not resolved_settings.query_reranker_enabled:
        return None
    if resolved_settings.query_reranker_provider == "local":
        if not resolved_settings.local_reranker_base_url.strip() or not resolved_settings.local_reranker_model.strip():
            return None
        return OpenAICompatibleReranker(
            api_key=resolved_settings.local_reranker_api_key,
            base_url=resolved_settings.local_reranker_base_url,
            model=resolved_settings.local_reranker_model,
            timeout_seconds=resolved_settings.local_reranker_timeout_seconds,
        )
    client = build_openrouter_client(resolved_settings, purpose="query")
    if client is None:
        return None
    return OpenRouterReranker(client=client)
