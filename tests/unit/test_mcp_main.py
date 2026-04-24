from __future__ import annotations

from pathlib import Path

from dory_core.types import SearchResp
from dory_mcp import server as mcp_server


def test_parse_serve_args_defaults() -> None:
    config = mcp_server.parse_serve_args([])

    assert config.mode == "stdio"
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.corpus_root == Path(".")
    assert config.index_root == Path(".index")


def test_main_dispatches_tcp(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_serve_tcp(core, host: str, port: int) -> None:
        calls["tcp"] = {"core": core, "host": host, "port": port}

    monkeypatch.setattr(mcp_server, "serve_tcp", fake_serve_tcp)

    mcp_server.main(["--mode", "tcp", "--host", "0.0.0.0", "--port", "9901"])

    assert calls["tcp"]["host"] == "0.0.0.0"
    assert calls["tcp"]["port"] == 9901


def test_query_retrieval_planner_respects_toggle(monkeypatch) -> None:
    calls: list[str] = []

    class FakePlanner:
        def __init__(self, client) -> None:
            self.client = client

    monkeypatch.setattr(mcp_server, "OpenRouterRetrievalPlanner", FakePlanner)
    monkeypatch.setattr(
        mcp_server,
        "build_openrouter_client",
        lambda settings, *, purpose: calls.append(purpose) or object(),
    )

    disabled = mcp_server.DorySettings(query_planner_enabled=False)
    enabled = mcp_server.DorySettings(query_planner_enabled=True)

    assert mcp_server._build_retrieval_planner(disabled, purpose="query") is None
    assert calls == []

    assert isinstance(mcp_server._build_retrieval_planner(enabled, purpose="query"), FakePlanner)
    assert calls == ["query"]


def test_runtime_core_reuses_search_engine(monkeypatch, tmp_path: Path, fake_embedder) -> None:
    constructed: list[object] = []

    class FakeSearchEngine:
        def __init__(self, *args, **kwargs) -> None:
            constructed.append(self)

        def search(self, req):
            return SearchResp(query=req.query, count=0, results=[], took_ms=1)

    monkeypatch.setattr(mcp_server, "SearchEngine", FakeSearchEngine)
    monkeypatch.setattr(mcp_server, "build_active_memory_components", lambda settings: (None, None))

    core = mcp_server.RuntimeCore(
        corpus_root=tmp_path / "corpus",
        index_root=tmp_path / "index",
        embedder=fake_embedder,
    )

    core.search({"query": "alpha"})
    core.search({"query": "beta"})

    assert len(constructed) == 1
    assert core.active_memory_engine.search_engine is core.search_engine
