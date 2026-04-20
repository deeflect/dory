from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_dory_console_entrypoint_help() -> None:
    result = subprocess.run(
        ["uv", "run", "dory", "--help"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Dory CLI" in result.stdout


def test_dory_http_console_entrypoint_help() -> None:
    result = subprocess.run(
        ["uv", "run", "dory-http", "--help"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the Dory HTTP server." in result.stdout
