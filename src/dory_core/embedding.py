from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS = 768
_DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"
_QUERY_TASK_TYPE = "RETRIEVAL_QUERY"
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


def build_runtime_embedder(settings: object | None = None) -> GeminiEmbedder:
    from dory_core.config import DorySettings

    resolved_settings = settings if isinstance(settings, DorySettings) else DorySettings()
    if not resolved_settings.gemini_api_key:
        raise EmbeddingConfigurationError(
            "Gemini embedding API key is missing. Set DORY_GEMINI_API_KEY, GOOGLE_API_KEY, or GEMINI_API_KEY."
        )

    inter_batch_delay = float(os.environ.get("DORY_EMBED_INTER_BATCH_DELAY", "0"))
    max_retries = int(os.environ.get("DORY_EMBED_MAX_RETRIES", "6"))
    return GeminiEmbedder(
        api_key=resolved_settings.gemini_api_key,
        model=resolved_settings.embedding_model,
        dimension=resolved_settings.embedding_dimensions,
        batch_size=resolved_settings.embedding_batch_size,
        max_retries=max_retries,
        inter_batch_delay=inter_batch_delay,
    )
