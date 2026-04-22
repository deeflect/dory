from __future__ import annotations

import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS = 768
_DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"
_QUERY_TASK_TYPE = "RETRIEVAL_QUERY"
DEFAULT_LOCAL_QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
_RETRYABLE_STATUSES = {"RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED"}
_RETRYABLE_CODES = {429, 503, 504}
_logger = logging.getLogger(__name__)


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    pass


@runtime_checkable
class ContentEmbedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbedder:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_EMBEDDING_DIMENSIONS,
        batch_size: int = 100,
        max_retries: int = 6,
        initial_backoff: float = 2.0,
        inter_batch_delay: float = 0.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.inter_batch_delay = inter_batch_delay
        self._client = genai.Client(api_key=api_key)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_batch(texts, task_type=_DOCUMENT_TASK_TYPE)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text], task_type=_QUERY_TASK_TYPE)[0]

    def _embed_batch(
        self,
        texts: Sequence[str],
        *,
        task_type: str,
    ) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        total_batches = (len(texts) + self.batch_size - 1) // self.batch_size
        for batch_index, start in enumerate(range(0, len(texts), self.batch_size), start=1):
            batch = list(texts[start : start + self.batch_size])
            response = self._invoke_with_retry(batch, task_type=task_type)

            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise EmbeddingProviderError(f"Gemini returned {len(embeddings)} embeddings for {len(batch)} inputs")

            for embedding in embeddings:
                values = embedding.values or []
                if len(values) != self.dimension:
                    raise EmbeddingProviderError(f"Gemini returned dimension {len(values)}; expected {self.dimension}")
                vectors.append([float(value) for value in values])

            if self.inter_batch_delay > 0 and batch_index < total_batches:
                time.sleep(self.inter_batch_delay)

        return vectors

    def _invoke_with_retry(
        self,
        batch: list[str],
        *,
        task_type: str,
    ):
        attempt = 0
        backoff = self.initial_backoff
        while True:
            try:
                return self._client.models.embed_content(
                    model=self.model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.dimension,
                    ),
                )
            except genai_errors.APIError as err:
                detail = getattr(err, "message", None) or str(err)
                status = getattr(err, "status", None)
                code = getattr(err, "code", None)
                retryable = status in _RETRYABLE_STATUSES or code in _RETRYABLE_CODES
                if retryable and attempt < self.max_retries:
                    sleep_for = backoff + random.uniform(0, backoff / 2)
                    _logger.warning(
                        "Gemini embedding retry %d/%d in %.1fs (status=%s, code=%s): %s",
                        attempt + 1,
                        self.max_retries,
                        sleep_for,
                        status,
                        code,
                        detail,
                    )
                    time.sleep(sleep_for)
                    attempt += 1
                    backoff = min(backoff * 2, 60.0)
                    continue
                suffix = []
                if code is not None:
                    suffix.append(f"code={code}")
                if status is not None:
                    suffix.append(f"status={status}")
                suffix_str = f" ({', '.join(suffix)})" if suffix else ""
                raise EmbeddingProviderError(f"Gemini embedding request failed{suffix_str}: {detail}") from err


@dataclass(frozen=True, slots=True)
class OpenAICompatibleEmbedder:
    api_key: str | None
    base_url: str
    request_model: str
    dimension: int = DEFAULT_EMBEDDING_DIMENSIONS
    batch_size: int = 100
    timeout_seconds: float = 30.0
    query_instruction: str | None = DEFAULT_LOCAL_QUERY_INSTRUCTION
    retries: int = 2

    @property
    def model(self) -> str:
        return f"openai-compatible:{self.request_model}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_batch(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([_format_query_instruction(text, self.query_instruction)])[0]

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            response_payload = self._invoke_with_retry(batch)
            vectors.extend(
                _parse_openai_embedding_payload(
                    response_payload,
                    expected_count=len(batch),
                    dimension=self.dimension,
                )
            )
        return vectors

    def _invoke_with_retry(self, batch: list[str]) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.request_model,
            "input": batch,
            "dimensions": self.dimension,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        with httpx.Client(base_url=_normalize_base_url(self.base_url), timeout=self.timeout_seconds) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = client.post("/embeddings", headers=headers, json=payload)
                except httpx.HTTPError as err:
                    last_error = err
                    if attempt >= self.retries:
                        raise EmbeddingProviderError(f"OpenAI-compatible embedding request failed: {err}") from err
                    _sleep_backoff(attempt)
                    continue

                if response.status_code < 400:
                    try:
                        parsed = response.json()
                    except ValueError as err:
                        raise EmbeddingProviderError("OpenAI-compatible embedding endpoint returned invalid JSON.") from err
                    if not isinstance(parsed, dict):
                        raise EmbeddingProviderError(
                            "OpenAI-compatible embedding endpoint returned a non-object payload."
                        )
                    return parsed
                if not _should_retry_status(response.status_code) or attempt >= self.retries:
                    raise EmbeddingProviderError(_format_error_response(response))
                _sleep_backoff(attempt, retry_after=response.headers.get("Retry-After"))

        if last_error is not None:
            raise EmbeddingProviderError(f"OpenAI-compatible embedding request failed: {last_error}") from last_error
        raise EmbeddingProviderError("OpenAI-compatible embedding request failed: unknown provider error")


def build_runtime_embedder(settings: object | None = None) -> GeminiEmbedder | OpenAICompatibleEmbedder:
    from dory_core.config import DorySettings

    resolved_settings = settings if isinstance(settings, DorySettings) else DorySettings()
    if resolved_settings.embedding_provider == "local":
        if not resolved_settings.local_embedding_base_url.strip():
            raise EmbeddingConfigurationError("Local embedding base URL is missing. Set DORY_LOCAL_EMBEDDING_BASE_URL.")
        if not resolved_settings.local_embedding_model.strip():
            raise EmbeddingConfigurationError("Local embedding model is missing. Set DORY_LOCAL_EMBEDDING_MODEL.")
        return OpenAICompatibleEmbedder(
            api_key=resolved_settings.local_embedding_api_key,
            base_url=resolved_settings.local_embedding_base_url,
            request_model=resolved_settings.local_embedding_model,
            dimension=resolved_settings.embedding_dimensions,
            batch_size=resolved_settings.embedding_batch_size,
            timeout_seconds=resolved_settings.local_embedding_timeout_seconds,
            query_instruction=resolved_settings.local_embedding_query_instruction.strip() or None,
        )

    if not resolved_settings.gemini_api_key:
        raise EmbeddingConfigurationError(
            "Gemini embedding API key is missing. Set DORY_GEMINI_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY. "
            "For offline/local embeddings, set DORY_EMBEDDING_PROVIDER=local, DORY_LOCAL_EMBEDDING_BASE_URL, "
            "DORY_LOCAL_EMBEDDING_MODEL, and DORY_EMBEDDING_DIMENSIONS."
        )

    return GeminiEmbedder(
        api_key=resolved_settings.gemini_api_key,
        model=resolved_settings.embedding_model,
        dimension=resolved_settings.embedding_dimensions,
        batch_size=resolved_settings.embedding_batch_size,
        max_retries=resolved_settings.embed_max_retries,
        inter_batch_delay=resolved_settings.embed_inter_batch_delay,
    )


def _parse_openai_embedding_payload(
    payload: dict[str, object],
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise EmbeddingProviderError("OpenAI-compatible embedding endpoint returned no data list.")
    if len(data) != expected_count:
        raise EmbeddingProviderError(
            f"OpenAI-compatible embedding endpoint returned {len(data)} embeddings for {expected_count} inputs"
        )

    rows: list[tuple[int, list[float]]] = []
    for fallback_index, item in enumerate(data):
        if not isinstance(item, dict):
            raise EmbeddingProviderError("OpenAI-compatible embedding endpoint returned a malformed data item.")
        raw_embedding = item.get("embedding")
        if not isinstance(raw_embedding, list):
            raise EmbeddingProviderError(
                "OpenAI-compatible embedding endpoint returned a data item without an embedding."
            )
        vector = [float(value) for value in raw_embedding]
        if len(vector) != dimension:
            raise EmbeddingProviderError(
                f"OpenAI-compatible embedding endpoint returned dimension {len(vector)}; expected {dimension}"
            )
        raw_index = item.get("index", fallback_index)
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as err:
            raise EmbeddingProviderError("OpenAI-compatible embedding endpoint returned a non-integer index.") from err
        rows.append((index, vector))

    rows.sort(key=lambda row: row[0])
    indexes = [index for index, _vector in rows]
    if indexes != list(range(expected_count)):
        raise EmbeddingProviderError("OpenAI-compatible embedding endpoint returned invalid indexes.")
    return [vector for _index, vector in rows]


def _format_query_instruction(text: str, instruction: str | None) -> str:
    normalized_instruction = instruction.strip() if instruction is not None else ""
    if not normalized_instruction:
        return text
    return f"Instruct: {normalized_instruction}\nQuery:{text}"


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _should_retry_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _sleep_backoff(attempt: int, *, retry_after: str | None = None) -> None:
    if retry_after is not None:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = None
        else:
            time.sleep(max(0.0, min(delay, 10.0)))
            return
    time.sleep(min(0.5 * (2**attempt), 4.0))


def _format_error_response(response: httpx.Response) -> str:
    detail = response.text
    try:
        payload = response.json()
    except ValueError:
        pass
    else:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = error["message"]
            elif isinstance(error, str):
                detail = error
    return f"OpenAI-compatible embedding request failed ({response.status_code}): {detail}"
