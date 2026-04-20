from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app as cli_app
from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.session_plane import SessionEvidencePlane
from dory_core.types import ActiveMemoryResp, SearchReq


class _StubActiveMemoryEngine:
    def __init__(self, response: ActiveMemoryResp) -> None:
        self.response = response
        self.requests: list[object] = []

    def build(self, req):  # pragma: no cover - simple test stub
        self.requests.append(req)
        return self.response


def test_search_corpus_selector_routes_to_durable_sessions_and_all(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    index_root = tmp_path / "index"
    reindex_corpus(sample_corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-12-s1.md",
        content="We used private mesh VPN in the session.",
        updated="2026-04-12T10:00:00Z",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
    )

    engine = SearchEngine(index_root, fake_embedder)

    durable = engine.search(SearchReq(query="private mesh VPN", mode="hybrid", corpus="durable", k=5))
    sessions = engine.search(SearchReq(query="private mesh VPN", mode="hybrid", corpus="sessions", k=5))
    all_results = engine.search(SearchReq(query="private mesh VPN", mode="hybrid", corpus="all", k=5))

    assert durable.results[0].path == "core/env.md"
    assert all(result.path.startswith("logs/sessions/") for result in sessions.results)
    assert all_results.results[0].path == "core/env.md"
    assert any(result.path.startswith("logs/sessions/") for result in all_results.results)


def test_active_memory_cli_command_uses_engine(cli_runner, monkeypatch, tmp_path: Path) -> None:
    response = ActiveMemoryResp(kind="none", block="", summary="", sources=[])
    stub = _StubActiveMemoryEngine(response)
    monkeypatch.setattr("dory_cli.main._build_active_memory_engine", lambda config: stub)

    result = cli_runner.invoke(
        cli_app,
        [
            "--corpus-root",
            str(tmp_path / "corpus"),
            "--index-root",
            str(tmp_path / "index"),
            "active-memory",
            "format this file",
            "--agent",
            "claude",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["kind"] == "none"
    assert stub.requests, "expected the command to call the active-memory engine"
