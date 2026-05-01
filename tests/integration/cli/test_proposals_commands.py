from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_proposals_create_show_apply_and_reject(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal state.\n",
        encoding="utf-8",
    )

    created = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "proposals",
            "create",
            "## Current State\n\nCLI proposal state.",
            "--proposal-id",
            "cli-proposal",
            "--subject",
            "active",
            "--action",
            "replace",
            "--kind",
            "state",
            "--scope",
            "core",
            "--agent",
            "codex",
        ],
    )
    listed = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "proposals", "list"],
    )
    shown = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "proposals", "show", "cli-proposal"],
    )
    applied = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "proposals", "apply", "cli-proposal"],
    )

    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.stdout)
    assert created_payload["proposal_id"] == "cli-proposal"
    assert created_payload["proposal"]["actions"][0]["dry_run"]["target_path"] == "core/active.md"
    assert listed.exit_code == 0, listed.output
    assert "cli-proposal" in listed.stdout
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["agent"] == "codex"
    assert applied.exit_code == 0, applied.output
    assert "core/active.md" in applied.stdout
    assert "CLI proposal state." in (corpus_root / "core" / "active.md").read_text(encoding="utf-8")

    rejected_create = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "proposals",
            "create",
            "Reject from CLI.",
            "--proposal-id",
            "cli-reject",
            "--subject",
            "active",
            "--kind",
            "note",
            "--scope",
            "core",
        ],
    )
    rejected = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "proposals",
            "reject",
            "cli-reject",
            "--reason",
            "not needed",
        ],
    )

    assert rejected_create.exit_code == 0, rejected_create.output
    assert rejected.exit_code == 0, rejected.output
    assert "inbox/rejected/cli-reject.json" in rejected.stdout
