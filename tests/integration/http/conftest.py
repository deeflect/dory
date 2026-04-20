from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def allow_no_auth_for_http_tests(monkeypatch) -> None:
    monkeypatch.setenv("DORY_ALLOW_NO_AUTH", "true")
