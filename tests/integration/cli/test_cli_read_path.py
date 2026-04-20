from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_cli_wake_returns_frozen_hot_block(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "wake",
            "--budget",
            "600",
            "--agent",
            "codex",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Casey builds agent infrastructure" in result.stdout
    assert "Dory should be direct" in result.stdout


def test_cli_search_returns_fixture_hit(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "search",
            "HomeServer",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] >= 1
    assert any(hit["path"] == "core/env.md" for hit in payload["results"])


def test_cli_get_returns_requested_slice(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "get",
            "core/user.md",
            "--from",
            "1",
            "-n",
            "10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "title: User" in result.stdout
    assert "Casey builds agent infrastructure" in result.stdout


def test_cli_status_reports_index_counts(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "status",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["api_version"] == "v1"
    assert payload["corpus_files"] == 7
    assert payload["files_indexed"] == 7
    assert payload["chunks_indexed"] >= 7


def test_cli_reindex_reports_summary(cli_runner, indexed_fixture_env) -> None:
    stale_marker = indexed_fixture_env["index_root"] / "stale.txt"
    stale_marker.write_text("stale", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "reindex",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["files_indexed"] == 7
    assert payload["chunks_indexed"] >= 7
    assert payload["vectors_indexed"] == payload["chunks_indexed"]
    assert not stale_marker.exists()
