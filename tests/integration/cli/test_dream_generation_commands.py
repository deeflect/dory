from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


class _QueuedOpenRouterClient:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads

    def generate_json(self, **kwargs):
        if not self.payloads:
            raise AssertionError("No queued OpenRouter payload left for test.")
        return self.payloads.pop(0)


def test_dream_distill_and_propose_commands(cli_runner, monkeypatch, tmp_path: Path, sample_corpus_root: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    session_path = corpus_root / "logs" / "sessions" / "codex" / "2026-04-10.md"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        "---\ntitle: Session\ncreated: 2026-04-10\ntype: session\nstatus: done\n---\n\nDecided to keep Dory reviewable.\n",
        encoding="utf-8",
    )

    client = _QueuedOpenRouterClient(
        [
            {
                "summary": "The session focused on reviewable proposals.",
                "key_facts": ["The session referenced Dory proposal review."],
                "decisions": ["Keep human review before apply."],
                "followups": ["Run eval again after changes."],
                "entities": ["Dory"],
            },
            {
                "actions": [
                    {
                        "action": "write",
                        "kind": "decision",
                        "subject": "dory",
                        "content": "Keep human review before apply.",
                        "scope": "project",
                        "confidence": "high",
                        "reason": "Grounded in distilled note.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(
        "dory_cli.main.build_openrouter_client",
        lambda settings=None: client,
    )

    distill_result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "dream",
            "distill",
            "logs/sessions/codex/2026-04-10.md",
        ],
    )
    assert distill_result.exit_code == 0, distill_result.output
    distilled_relative = distill_result.stdout.strip()
    distilled_path = corpus_root / distilled_relative
    assert distilled_path.exists()
    assert "## Decisions" in distilled_path.read_text(encoding="utf-8")

    propose_result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "dream",
            "propose",
            "codex-2026-04-10",
        ],
    )
    assert propose_result.exit_code == 0, propose_result.output
    proposal_path = corpus_root / propose_result.stdout.strip()
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert payload["actions"][0]["action"] == "write"
    assert payload["actions"][0]["subject"] == "dory"


def test_maintain_inspect_command_writes_report(cli_runner, monkeypatch, tmp_path: Path, sample_corpus_root: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    client = _QueuedOpenRouterClient(
        [
            {
                "suggested_type": "project",
                "suggested_status": "active",
                "suggested_area": "coding",
                "suggested_canonical": True,
                "suggested_source_kind": "human",
                "suggested_temperature": "warm",
                "suggested_target": "projects/dory/state.md",
                "rationale": "This reads like project state.",
                "confidence": 0.84,
            }
        ]
    )
    monkeypatch.setattr(
        "dory_cli.main.build_openrouter_client",
        lambda settings=None: client,
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "maintain",
            "inspect",
            "core/active.md",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["suggested_type"] == "project"
    assert payload["report_path"] == "inbox/maintenance/core--active.json"
