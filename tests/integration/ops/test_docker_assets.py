from __future__ import annotations

from pathlib import Path


def test_docker_assets_reference_expected_ports() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "8765" in compose
    assert "8766" in compose
    assert "/healthz" in compose
    assert "DORY_ALLOW_NO_AUTH: ${DORY_ALLOW_NO_AUTH:-false}" in compose
    assert "uv" in dockerfile
    assert "DORY_ALLOW_NO_AUTH" not in dockerfile
    assert "ghcr.io" in workflow
