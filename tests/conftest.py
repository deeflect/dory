from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dory_core.index.reindex import reindex_corpus


class FakeEmbedder:
    dimension = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index) for index in range(self.dimension)] for _ in texts]


@pytest.fixture
def sample_corpus_root() -> Path:
    return Path("tests/fixtures/dory_sample")


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def patch_runtime_embedder_factories(monkeypatch) -> None:
    from dory_cli import eval as cli_eval
    from dory_cli import main as cli_main
    from dory_core.llm import openrouter as openrouter_module
    from dory_http import app as http_app
    from dory_mcp import server as mcp_server

    monkeypatch.setattr(cli_eval, "build_runtime_embedder", lambda settings=None: FakeEmbedder())
    monkeypatch.setattr(cli_eval, "build_reranker", lambda settings=None: None)
    monkeypatch.setattr(cli_main, "build_runtime_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(cli_main, "build_openrouter_client", lambda settings=None, purpose=None: None)
    monkeypatch.setattr(cli_main, "build_reranker", lambda settings=None: None)
    monkeypatch.setattr(http_app, "build_runtime_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(http_app, "build_openrouter_client", lambda settings=None, purpose=None: None)
    monkeypatch.setattr(http_app, "build_reranker", lambda settings=None: None)
    monkeypatch.setattr(mcp_server, "build_runtime_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(openrouter_module, "_fetch_openrouter_models_catalog", lambda base_url, timeout_seconds: ())


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def indexed_fixture_env(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder: FakeEmbedder,
) -> dict[str, object]:
    index_root = tmp_path / ".index"
    result = reindex_corpus(sample_corpus_root, index_root, fake_embedder)
    return {
        "corpus_root": sample_corpus_root,
        "index_root": index_root,
        "embedder": fake_embedder,
        "reindex_result": result,
    }
