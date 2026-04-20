from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from dory_core.config import DorySettings
from dory_core.embedding import ContentEmbedder, EmbeddingConfigurationError, GeminiEmbedder, build_runtime_embedder


class FakeEmbedder:
    dimension = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i) for i in range(self.dimension)] for _ in texts]


def test_content_embedder_protocol_returns_fixed_dim_vectors() -> None:
    fake_embedder = cast(ContentEmbedder, FakeEmbedder())

    assert isinstance(fake_embedder, ContentEmbedder)
    vectors = fake_embedder.embed(["hello"])
    assert len(vectors[0]) == 768


def test_build_runtime_embedder_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DORY_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(EmbeddingConfigurationError):
        build_runtime_embedder(DorySettings(_env_file=None))


def test_build_runtime_embedder_uses_google_api_key_alias(monkeypatch) -> None:
    monkeypatch.delenv("DORY_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")

    embedder = build_runtime_embedder(DorySettings(_env_file=None))

    assert isinstance(embedder, GeminiEmbedder)
    assert embedder.api_key == "google-key"
    assert embedder.model == "gemini-embedding-001"
    assert embedder.dimension == 768


def test_gemini_embedder_uses_expected_task_types(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            self.models = self
            self.api_key = api_key

        def embed_content(self, *, model: str, contents: list[str], config) -> object:
            calls.append(
                {
                    "model": model,
                    "contents": list(contents),
                    "task_type": config.task_type,
                    "output_dimensionality": config.output_dimensionality,
                }
            )
            embeddings = [
                SimpleNamespace(values=[float(index) for index in range(config.output_dimensionality)])
                for _ in contents
            ]
            return SimpleNamespace(embeddings=embeddings)

    monkeypatch.setattr("dory_core.embedding.genai.Client", FakeClient)
    embedder = GeminiEmbedder(api_key="example", dimension=4, batch_size=2)

    vectors = embedder.embed(["alpha", "beta", "gamma"])
    query_vector = embedder.embed_query("needle")

    assert len(vectors) == 3
    assert len(query_vector) == 4
    assert calls[0]["task_type"] == "RETRIEVAL_DOCUMENT"
    assert calls[0]["contents"] == ["alpha", "beta"]
    assert calls[1]["contents"] == ["gamma"]
    assert calls[2]["task_type"] == "RETRIEVAL_QUERY"
    assert calls[2]["output_dimensionality"] == 4
