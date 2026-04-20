from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_script(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = None if env is None else {**os.environ, **env}
    return subprocess.run(
        ["bash", *args],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=merged_env,
    )


def test_install_dory_script_help_mentions_host_client_and_harnesses() -> None:
    result = _run_script("scripts/ops/install-dory.sh", "--help")

    assert result.returncode == 0, result.stderr
    assert "host" in result.stdout
    assert "client" in result.stdout
    assert "solo" in result.stdout
    assert "Claude" in result.stdout
    assert "Codex" in result.stdout
    assert "Opencode" in result.stdout


def test_client_launchd_helper_help_is_available() -> None:
    result = _run_script("scripts/ops/install-client-launchd.sh", "--help")

    assert result.returncode == 0, result.stderr
    assert "launchd" in result.stdout


def test_client_systemd_helper_help_is_available() -> None:
    result = _run_script("scripts/ops/install-client-systemd.sh", "--help")

    assert result.returncode == 0, result.stderr
    assert "systemd" in result.stdout


def test_install_dory_host_writes_env_file(tmp_path: Path) -> None:
    repo_root = _repo_root()
    config_dir = tmp_path / "config"
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "DORY_CONFIG_DIR": str(config_dir),
        "DORY_CORPUS_ROOT": str(tmp_path / "corpus"),
        "DORY_INDEX_ROOT": str(tmp_path / "index"),
    }

    result = _run_script(
        "scripts/ops/install-dory.sh",
        "host",
        "--repo-root",
        str(repo_root),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    host_env = (config_dir / "host.env").read_text(encoding="utf-8")
    assert "DORY_CORPUS_ROOT=" in host_env
    assert "DORY_INDEX_ROOT=" in host_env
    assert "Host configuration written to" in result.stdout


def test_install_dory_client_writes_env_file(tmp_path: Path) -> None:
    repo_root = _repo_root()
    config_dir = tmp_path / "config"
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "DORY_CONFIG_DIR": str(config_dir),
        "DORY_HTTP_URL": "http://127.0.0.1:8766",
        "DORY_CLIENT_HARNESSES": "codex opencode",
    }

    result = _run_script(
        "scripts/ops/install-dory.sh",
        "client",
        "--repo-root",
        str(repo_root),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    client_env = (config_dir / "client.env").read_text(encoding="utf-8")
    assert "DORY_HTTP_URL=" in client_env
    assert "DORY_CLIENT_HARNESSES=" in client_env
    assert "DORY_CLIENT_CHECKPOINTS_PATH=" in client_env
    assert "DORY_CLIENT_POLL_SECONDS=" in client_env
    assert "DORY_CLAUDE_PROJECTS_ROOT=" in client_env
    assert "DORY_CODEX_SESSIONS_ROOT=" in client_env
    assert "DORY_OPENCODE_DB_PATH=" in client_env
    assert "--watch" in client_env
    assert "Selected harnesses: codex opencode" in result.stdout


def test_install_dory_solo_writes_host_and_client_env_files(tmp_path: Path) -> None:
    repo_root = _repo_root()
    config_dir = tmp_path / "config"
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "DORY_CONFIG_DIR": str(config_dir),
        "DORY_CORPUS_ROOT": str(tmp_path / "corpus"),
        "DORY_INDEX_ROOT": str(tmp_path / "index"),
        "DORY_CLIENT_HARNESSES": "claude codex",
    }

    result = _run_script(
        "scripts/ops/install-dory.sh",
        "solo",
        "--repo-root",
        str(repo_root),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    host_env = (config_dir / "host.env").read_text(encoding="utf-8")
    client_env = (config_dir / "client.env").read_text(encoding="utf-8")
    assert "DORY_CORPUS_ROOT=" in host_env
    assert "DORY_INDEX_ROOT=" in host_env
    assert "DORY_HTTP_URL=" in client_env
    assert "DORY_CLIENT_HARNESSES=" in client_env
    assert "DORY_CLIENT_CHECKPOINTS_PATH=" in client_env
    assert "DORY_CLAUDE_PROJECTS_ROOT=" in client_env
    assert "DORY_CODEX_SESSIONS_ROOT=" in client_env
    assert "DORY_OPENCODE_DB_PATH=" in client_env
    assert "--watch" in client_env
    assert "Solo configuration written to" in result.stdout
    assert "Selected harnesses: claude codex" in result.stdout
