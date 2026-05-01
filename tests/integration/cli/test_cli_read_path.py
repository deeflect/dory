from __future__ import annotations

import json

from dory_core.session_plane import SessionEvidencePlane
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


def test_cli_search_can_scope_session_recall(cli_runner, indexed_fixture_env) -> None:
    index_root = indexed_fixture_env["index_root"]
    plane = SessionEvidencePlane(index_root / "session_plane.db")
    plane.upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-30-codex.md",
        content="Scoped CLI recall found the Dory session-key marker.",
        updated="2026-04-30T12:00:00Z",
        agent="codex",
        device="mac",
        session_id="codex-cli",
        status="done",
    )
    plane.upsert_session_chunk(
        path="logs/sessions/hermes/zima/2026-04-30-hermes.md",
        content="Scoped CLI recall found the Dory session-key marker.",
        updated="2026-04-30T12:00:00Z",
        agent="hermes",
        device="zima",
        session_id="hermes-cli",
        status="done",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(index_root),
            "search",
            "Dory session-key marker",
            "--corpus",
            "sessions",
            "--mode",
            "recall",
            "--agent",
            "codex",
            "--session-key",
            "codex-cli",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [hit["path"] for hit in payload["results"]] == ["logs/sessions/codex/mac/2026-04-30-codex.md"]


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
    assert payload["corpus_files"] == 6
    assert payload["session_files"] == 1
    assert payload["files_indexed"] == 6
    assert payload["chunks_indexed"] >= 6


def test_cli_reindex_reports_summary(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "reindex",
            "--rebuild",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["files_indexed"] == 6
    assert payload["chunks_indexed"] >= 6
    assert payload["vectors_indexed"] == payload["chunks_indexed"]
    assert payload["session_sync"]["docs_indexed"] == 1


def test_cli_reindex_default_is_reconcile_no_op(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "reindex",
            "--no-progress",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["plan"]["unchanged_count"] == 6
    assert payload["plan"]["new_paths"] == []
    assert payload["plan"]["changed_paths"] == []
    assert payload["plan"]["orphan_paths"] == []
    assert payload["files_indexed"] == 0
    assert payload["orphans_removed"] == 0
    assert payload["session_sync"]["docs_indexed"] == 1


def test_cli_reindex_plan_is_dry_run(cli_runner, indexed_fixture_env) -> None:
    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(indexed_fixture_env["corpus_root"]),
            "--index-root",
            str(indexed_fixture_env["index_root"]),
            "reindex",
            "--plan",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "unchanged_count" in payload
    assert payload["unchanged_count"] == 6
    assert payload["session_plane"]["session_files"] == 1
