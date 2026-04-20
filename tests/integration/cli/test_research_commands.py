from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


class _FakeResearchEngine:
    def __init__(self, search_engine) -> None:
        self.search_engine = search_engine

    def research_from_req(self, req):
        from dory_core.types import ArtifactReq, ResearchResp

        artifact = ArtifactReq(
            kind=req.kind,
            title=req.question.rstrip("?"),
            question=req.question,
            body="Rooster is the active focus this week.",
            sources=["core/active.md"],
        )
        return ResearchResp(artifact=artifact, sources=["core/active.md"])


def test_cli_research_writes_artifact_and_returns_path(
    cli_runner,
    monkeypatch,
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"

    class _FakeEmbedder:
        dimension = 1

        def embed(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(
        "dory_cli.main.build_runtime_embedder",
        _FakeEmbedder,
    )
    monkeypatch.setattr("dory_cli.main.ResearchEngine", lambda search_engine: _FakeResearchEngine(search_engine))

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "research",
            "What are we working on right now?",
            "--kind",
            "report",
            "--corpus",
            "all",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["artifact"]["path"].startswith("references/reports/")
    assert (corpus_root / payload["artifact"]["path"]).exists()
    assert "core/active.md" in payload["research"]["sources"]


def test_cli_research_briefing_writes_briefing_path(
    cli_runner,
    monkeypatch,
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"

    class _FakeEmbedder:
        dimension = 1

        def embed(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(
        "dory_cli.main.build_runtime_embedder",
        _FakeEmbedder,
    )
    monkeypatch.setattr("dory_cli.main.ResearchEngine", lambda search_engine: _FakeResearchEngine(search_engine))

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "research",
            "Summarize Rooster.",
            "--kind",
            "briefing",
            "--corpus",
            "all",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["artifact"]["path"].startswith("references/briefings/")
    assert (corpus_root / payload["artifact"]["path"]).exists()
    assert "core/active.md" in payload["research"]["sources"]


def test_cli_wiki_refresh_indexes_writes_indexes(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    wiki_root = corpus_root / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\ntitle: Rooster\ntype: wiki\nstatus: active\n---\n\nRooster.\n",
        encoding="utf-8",
    )
    index_root = tmp_path / "index"

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-refresh-indexes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "wiki/index.md" in payload["written"]
    assert (corpus_root / "wiki" / "index.md").exists()
