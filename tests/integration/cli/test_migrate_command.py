from __future__ import annotations

import json
from pathlib import Path

from typer.main import get_command

from dory_cli.main import app


def test_migrate_command_bootstraps_canonical_pages(cli_runner, tmp_path: Path) -> None:
    output_root = tmp_path / "corpus"
    legacy_root = Path("tests/fixtures/legacy_clawd_brain")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(output_root),
            "migrate",
            "--no-llm",
            str(legacy_root),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["staged_count"] >= 1
    assert payload["written_count"] >= 1
    assert payload["canonical_created_count"] == 0
    assert payload["quarantined_count"] == 0
    assert payload["stats"]["fallback_classified_count"] >= 1
    assert payload["stats"]["atom_count"] == 0
    assert (output_root / "sources" / "imported" / "user.md").exists()
    assert not (output_root / "projects" / "rooster" / "state.md").exists()
    assert (output_root / "references" / "reports" / "migrations").exists()


def test_migrate_command_can_estimate_sample_scope(cli_runner, tmp_path: Path) -> None:
    output_root = tmp_path / "corpus"
    legacy_root = Path("tests/fixtures/legacy_clawd_brain")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(output_root),
            "migrate",
            "--estimate",
            "--sample",
            "2",
            str(legacy_root),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scope"]["mode"] == "sample"
    assert payload["scope"]["sample_size"] == 2
    assert payload["selected"]["markdown_count"] == 2
    assert payload["estimate"]["estimated_total_tokens"] > 0


def test_migrate_command_help_mentions_parallel_jobs(cli_runner) -> None:
    result = cli_runner.invoke(app, ["migrate", "--help"], color=False, terminal_width=160)

    assert result.exit_code == 0, result.output
    migrate_command = get_command(app).commands["migrate"]
    assert any("--jobs" in param.opts for param in migrate_command.params)
    assert any("--interactive" in param.opts for param in migrate_command.params)


def test_migrate_command_interactive_refuses_non_tty(cli_runner, tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "a.md").write_text("# A\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["migrate", "--interactive", str(legacy_root)])

    assert result.exit_code == 2, result.output
    assert "interactive tty" in result.output.lower()
