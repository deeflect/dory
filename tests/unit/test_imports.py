from __future__ import annotations

from importlib import import_module


def test_packages_import() -> None:
    for name in ["dory_core", "dory_cli", "dory_mcp", "dory_http"]:
        assert import_module(name) is not None
