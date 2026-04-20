from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from dory_cli.main import app


def test_cli_purge_defaults_to_dry_run_without_embedder(cli_runner, tmp_path: Path, monkeypatch) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox" / "cli-purge.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: CLI purge\ntype: capture\n---\n\nTemporary.\n", encoding="utf-8")
    monkeypatch.setattr(
        "dory_cli.main.build_runtime_embedder",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run purge must not build embeddings")),
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(tmp_path / "index"),
            "purge",
            "inbox/cli-purge.md",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["action"] == "would_purge"
    assert target.exists()


def test_cli_purge_live_delete_requires_hash(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox" / "cli-purge.md"
    target.parent.mkdir(parents=True)
    text = "---\ntitle: CLI purge\ntype: capture\n---\n\nTemporary.\n"
    target.write_text(text, encoding="utf-8")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(tmp_path / "index"),
            "purge",
            "inbox/cli-purge.md",
            "--no-dry-run",
            "--reason",
            "cleanup",
            "--expected-hash",
            f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["action"] == "purged"
    assert not target.exists()
