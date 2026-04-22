from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app
from dory_core.llm.dream import DreamLLM
from dory_core.openclaw_parity import OpenClawParityStore
from dory_core.types import RecallEventReq


class _QueuedOpenRouterClient:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads

    def generate_json(self, **kwargs):
        if not self.payloads:
            raise AssertionError("No queued OpenRouter payload left for test.")
        return self.payloads.pop(0)


def test_ops_dream_once_writes_distilled_and_proposed(
    cli_runner, monkeypatch, tmp_path: Path, sample_corpus_root: Path
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    session = corpus_root / "logs" / "sessions" / "codex" / "2026-04-11.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        "---\ntitle: Session\ncreated: 2026-04-11\ntype: session\nstatus: done\n---\n\nDiscussed Dory operator jobs.\n",
        encoding="utf-8",
    )

    client = _QueuedOpenRouterClient(
        [
            {
                "summary": "The session focused on operator jobs.",
                "key_facts": ["Operator jobs should stay reviewable."],
                "decisions": ["Ship operator-first before daemon mode."],
                "followups": ["Run eval after job rollout."],
                "entities": ["Dory"],
            },
            {
                "actions": [
                    {
                        "action": "write",
                        "kind": "decision",
                        "subject": "dory",
                        "content": "Ship operator-first before daemon mode.",
                        "scope": "project",
                        "confidence": "high",
                        "reason": "Grounded in distilled session.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr("dory_cli.main.require_dream_llm", lambda settings: DreamLLM(client=client, backend="openrouter"))

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "dream-once",
            "--session",
            "logs/sessions/codex/2026-04-11.md",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["distilled"] == ["inbox/distilled/codex-2026-04-11.md"]
    assert payload["proposed"] == ["inbox/proposed/codex-2026-04-11.json"]


def test_ops_dream_once_defaults_to_digest_sources(cli_runner, monkeypatch, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    sessions_root = corpus_root / "logs" / "sessions" / "codex"
    sessions_root.mkdir(parents=True, exist_ok=True)
    (sessions_root / "2026-04-11.md").write_text(
        "---\ntitle: Session\ncreated: 2026-04-11\ntype: session\nstatus: done\n---\n\nDiscussed raw session.\n",
        encoding="utf-8",
    )
    digest = corpus_root / "digests" / "daily" / "2026-04-11.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text(
        "---\ntitle: Daily Digest\ntype: digest-daily\n---\n\nDecided to keep digest-first dreaming.\n",
        encoding="utf-8",
    )

    client = _QueuedOpenRouterClient(
        [
            {"actions": []},
        ]
    )
    monkeypatch.setattr("dory_cli.main.require_dream_llm", lambda settings: DreamLLM(client=client, backend="openrouter"))

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "dream-once",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["distilled"] == []
    assert payload["proposed"] == ["inbox/proposed/2026-04-11.json"]
    assert not (corpus_root / "inbox" / "distilled" / "codex-2026-04-11.md").exists()


def test_ops_dream_once_promotes_recall_candidates(cli_runner, monkeypatch, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "people").mkdir(parents=True, exist_ok=True)
    (corpus_root / "people" / "anna.md").write_text("# Anna\n", encoding="utf-8")

    store = OpenClawParityStore(index_root)
    for query in ("who is anna", "anna prefs"):
        store.record_recall_event(
            RecallEventReq(
                agent="openclaw",
                session_key="sess-4",
                query=query,
                result_paths=["people/anna.md"],
                selected_path="people/anna.md",
                corpus="memory",
                source="openclaw-recall",
            )
        )

    client = _QueuedOpenRouterClient(
        [
            {
                "actions": [
                    {
                        "action": "write",
                        "kind": "fact",
                        "subject": "anna",
                        "content": "Frequently recalled across distinct queries.",
                        "scope": "person",
                        "confidence": "medium",
                        "reason": "Grounded in recall-promotion note.",
                        "source": "recall-promotion",
                        "soft": False,
                    }
                ]
            }
        ]
    )
    monkeypatch.setattr("dory_cli.main.require_dream_llm", lambda settings: DreamLLM(client=client, backend="openrouter"))

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "dream-once",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["distilled"] == ["inbox/distilled/recall-people-anna.md"]
    assert payload["proposed"] == ["inbox/proposed/recall-people-anna.json"]


def test_ops_maintain_once_writes_reports(cli_runner, monkeypatch, tmp_path: Path, sample_corpus_root: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    client = _QueuedOpenRouterClient(
        [
            {
                "suggested_type": "core",
                "suggested_status": "active",
                "suggested_area": "personal",
                "suggested_canonical": True,
                "suggested_source_kind": "human",
                "suggested_temperature": "hot",
                "suggested_target": "core/user.md",
                "rationale": "Still a hot identity doc.",
                "confidence": 0.9,
            }
        ]
    )
    monkeypatch.setattr("dory_cli._internals.build_openrouter_client", lambda settings=None: client)

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "maintain-once",
            "--path",
            "core/user.md",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["reports"] == ["inbox/maintenance/core--user.json"]


def test_ops_wiki_refresh_once_writes_compiled_page(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    source = corpus_root / "core" / "active.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
title: Rooster
type: core
status: active
canonical: true
source_kind: human
temperature: hot
updated: 2026-04-13
---

Rooster is the active focus this week.
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-refresh-once",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "wiki/projects/rooster.md" in payload["written"]
    assert "wiki/index.md" in payload["written"]
    assert "wiki/hot.md" in payload["written"]
    assert "wiki/log.md" in payload["written"]


def test_ops_eval_once_writes_eval_artifacts(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "ops",
            "eval-once",
            "--no-reindex",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["run_id"]
    assert payload["summary_path"].endswith("summary.md")
    assert payload["results_path"].endswith("results.json")


def test_ops_watch_degrades_cleanly_without_openrouter(cli_runner, monkeypatch, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("dory_cli._internals.build_runtime_embedder", lambda: object())
    monkeypatch.setattr("dory_cli.main.build_dream_llm", lambda settings: None)

    started: dict[str, object] = {}

    class _FakeRunner:
        def __init__(self, **kwargs) -> None:
            started.update(kwargs)

        def serve_forever(self, *, poll_interval: float = 0.25) -> None:
            started["poll_interval"] = poll_interval

    monkeypatch.setattr("dory_cli.main.OpsWatchRunner", _FakeRunner)

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "watch",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dream"] is False
    assert payload["dream_requested"] is True
    assert "dream mode disabled" in payload["warning"]
    assert started["dream_runner"] is None
