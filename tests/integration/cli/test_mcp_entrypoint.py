from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_dory_mcp_console_entrypoint_help() -> None:
    result = subprocess.run(
        ["uv", "run", "dory-mcp", "--help"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the Dory MCP bridge." in result.stdout
