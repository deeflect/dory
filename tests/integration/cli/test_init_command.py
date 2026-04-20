from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_init_creates_runtime_layout(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "--auth-tokens-path",
            str(auth_tokens_path),
            "init",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["initialized"] is True
    assert corpus_root.joinpath("core", "user.md").exists()
    assert corpus_root.joinpath("core", "soul.md").exists()
    assert corpus_root.joinpath("core", "env.md").exists()
    assert corpus_root.joinpath("core", "active.md").exists()
    assert corpus_root.joinpath("inbox", "proposed").exists()
    assert index_root.exists()
    assert auth_tokens_path.read_text(encoding="utf-8") == "{}\n"


def test_init_does_not_overwrite_existing_core_files(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"
    user_path = corpus_root / "core" / "user.md"
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text("keep me\n", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "--auth-tokens-path",
            str(auth_tokens_path),
            "init",
        ],
    )

    assert result.exit_code == 0, result.output
    assert user_path.read_text(encoding="utf-8") == "keep me\n"
