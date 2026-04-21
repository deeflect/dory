from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from dory_core.config import DorySettings
from dory_core.embedding import (
    ContentEmbedder,
    EmbeddingConfigurationError,
    GeminiEmbedder,
    OpenAICompatibleEmbedder,
    build_runtime_embedder,
)


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


def test_build_runtime_embedder_can_use_local_openai_compatible_provider() -> None:
    embedder = build_runtime_embedder(
        DorySettings(
            _env_file=None,
            embedding_provider="local",
            local_embedding_api_key="placeholder",
            local_embedding_base_url="https://llm.example.test/v1",
            local_embedding_model="qwen3-embed",
            embedding_dimensions=1024,
        )
    )

    assert isinstance(embedder, OpenAICompatibleEmbedder)
    assert embedder.api_key == "placeholder"
    assert embedder.base_url == "https://llm.example.test/v1"
    assert embedder.request_model == "qwen3-embed"
    assert embedder.model == "openai-compatible:qwen3-embed"
    assert embedder.dimension == 1024
    assert embedder.query_instruction == "Given a web search query, retrieve relevant passages that answer the query"


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


def test_openai_compatible_embedder_uses_embeddings_endpoint(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = ""

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            }

    class FakeClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            calls.append({"base_url": base_url, "timeout": timeout})

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def post(self, path: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            calls.append({"path": path, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr("dory_core.embedding.httpx.Client", FakeClient)
    embedder = OpenAICompatibleEmbedder(
        api_key="secret",
        base_url="https://llm.example.test",
        request_model="qwen3-embed",
        dimension=2,
    )

    vectors = embedder.embed(["first", "second"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert calls[0] == {"base_url": "https://llm.example.test/v1", "timeout": 30.0}
    request = calls[1]
    assert request["path"] == "/embeddings"
    assert request["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer secret"}
    assert request["json"] == {"model": "qwen3-embed", "input": ["first", "second"], "dimensions": 2}


def test_openai_compatible_embedder_adds_instruction_to_query_only(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = ""

        def json(self) -> dict[str, object]:
            return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

    class FakeClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            calls.append({"base_url": base_url, "timeout": timeout})

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def post(self, path: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            calls.append({"path": path, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr("dory_core.embedding.httpx.Client", FakeClient)
    embedder = OpenAICompatibleEmbedder(
        api_key=None,
        base_url="https://llm.example.test/v1",
        request_model="qwen3-embed",
        dimension=2,
        query_instruction="Find relevant Dory memory notes",
    )

    embedder.embed(["plain document"])
    embedder.embed_query("needle")

    document_request = calls[1]
    query_request = calls[3]
    assert document_request["json"]["input"] == ["plain document"]
    assert query_request["json"]["input"] == ["Instruct: Find relevant Dory memory notes\nQuery:needle"]


def test_openai_compatible_embedder_can_disable_query_instruction(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = ""

        def json(self) -> dict[str, object]:
            return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

    class FakeClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            calls.append({"base_url": base_url, "timeout": timeout})

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def post(self, path: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            calls.append({"path": path, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr("dory_core.embedding.httpx.Client", FakeClient)
    embedder = OpenAICompatibleEmbedder(
        api_key=None,
        base_url="https://llm.example.test/v1",
        request_model="qwen3-embed",
        dimension=2,
        query_instruction=None,
    )

    embedder.embed_query("needle")

    request = calls[1]
    assert request["json"]["input"] == ["needle"]
