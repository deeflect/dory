from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def _load_checker():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "release" / "check-public-safety.py"
    module_name = "release_safety_checker"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def test_scan_git_tracked_reports_file_line_matches(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_path = "/Users/" + "example/private"
    secret_assignment = "OPENAI_API_" + "KEY=sk_" + "live_1234567890"

    assert _git("init", cwd=repo_root).returncode == 0
    (repo_root / "public.md").write_text(
        f"safe\nPrivatePersonAlpha met us at {local_path}\n",
        encoding="utf-8",
    )
    (repo_root / "secrets.env").write_text(f"{secret_assignment}\n", encoding="utf-8")
    assert _git("add", ".", cwd=repo_root).returncode == 0

    monkeypatch.setenv("DORY_PUBLIC_SAFETY_PRIVATE_TERMS", "PrivatePersonAlpha")
    checker = _load_checker()
    findings = checker.scan_git_tracked(repo_root)

    formatted = [finding.format() for finding in findings]
    assert "public.md:2: configured private term: PrivatePersonAlpha" in formatted
    assert f"public.md:2: private local path: {local_path}" in formatted
    assert "secrets.env:1: secret-like assignment: " + "OPENAI_API_KEY=sk***90" in formatted


def test_scan_path_flags_export_directory(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    nested = export_root / "bundle" / "release.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("PrivatePersonAlpha and private.example.invalid should not ship.\n", encoding="utf-8")

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "release" / "check-public-safety.py"
    env = {**os.environ, "DORY_PUBLIC_SAFETY_PRIVATE_TERMS": "PrivatePersonAlpha,private.example.invalid"}
    result = subprocess.run(
        [sys.executable, str(script_path), "--path", str(export_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "bundle/release.txt:1: configured private term: PrivatePersonAlpha" in result.stderr
    assert "bundle/release.txt:1: configured private term: private.example.invalid" in result.stderr


def test_cli_preserves_relative_explicit_path_in_output(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    (export_root / "release.txt").write_text("PrivatePetAlpha\n", encoding="utf-8")

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "release" / "check-public-safety.py"
    env = {**os.environ, "DORY_PUBLIC_SAFETY_PRIVATE_TERMS": "PrivatePetAlpha"}
    result = subprocess.run(
        [sys.executable, str(script_path), "--path", "release.txt"],
        cwd=export_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "release.txt:1: configured private term: PrivatePetAlpha" in result.stderr


def test_scan_path_allows_synthetic_safe_content(tmp_path: Path, monkeypatch) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    (export_root / "README.txt").write_text(
        "Example values only:\nOPENAI_API_KEY=example\n203.0.113.1 is mentioned in a placeholder note\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("DORY_PUBLIC_SAFETY_PRIVATE_TERMS", raising=False)
    checker = _load_checker()
    findings = checker.scan_path(export_root)

    assert findings == []


def test_cli_exits_nonzero_when_findings_exist(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    assert _git("init", cwd=repo_root).returncode == 0
    (repo_root / "report.md").write_text("PrivatePetAlpha\n", encoding="utf-8")
    assert _git("add", ".", cwd=repo_root).returncode == 0

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "release" / "check-public-safety.py"
    env = {**os.environ, "DORY_PUBLIC_SAFETY_PRIVATE_TERMS": "PrivatePetAlpha"}
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "report.md:1: configured private term: PrivatePetAlpha" in result.stderr


def test_scan_zip_archive_members(tmp_path: Path, monkeypatch) -> None:
    archive_path = tmp_path / "dist" / "bundle.whl"
    archive_path.parent.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("package/public.py", "print('safe')\n")
        archive.writestr("package/leak.txt", "PrivatePersonAlpha should not ship.\n")

    monkeypatch.setenv("DORY_PUBLIC_SAFETY_PRIVATE_TERMS", "PrivatePersonAlpha")
    checker = _load_checker()
    findings = checker.scan_path(archive_path)

    assert [finding.format().split("/dist/", maxsplit=1)[1] for finding in findings] == [
        "bundle.whl!package/leak.txt:1: configured private term: PrivatePersonAlpha"
    ]


def test_scan_tar_gz_archive_members(tmp_path: Path, monkeypatch) -> None:
    archive_path = tmp_path / "dist" / "bundle.tar.gz"
    archive_path.parent.mkdir()
    member_path = tmp_path / "release.txt"
    member_path.write_text("private.example.invalid should not ship.\n", encoding="utf-8")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(member_path, arcname="bundle/release.txt")

    monkeypatch.setenv("DORY_PUBLIC_SAFETY_PRIVATE_TERMS", "private.example.invalid")
    checker = _load_checker()
    findings = checker.scan_path(archive_path)

    assert [finding.format().split("/dist/", maxsplit=1)[1] for finding in findings] == [
        "bundle.tar.gz!bundle/release.txt:1: configured private term: private.example.invalid",
    ]
