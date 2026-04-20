from __future__ import annotations

from dory_cli import main as cli_main
from dory_core.config import DorySettings
from dory_core.llm_rerank import build_reranker
from dory_http import app as http_app


class _FakePlannerClient:
    pass


def test_http_query_planner_can_be_disabled(monkeypatch) -> None:
    settings = DorySettings(query_planner_enabled=False)
    calls: list[str] = []

    def fake_build_openrouter_client(settings: DorySettings | None = None, *, purpose: str = "default"):
        del settings
        calls.append(purpose)
        return _FakePlannerClient()

    monkeypatch.setattr(http_app, "build_openrouter_client", fake_build_openrouter_client)

    planner = http_app._build_retrieval_planner(settings, purpose="query")

    assert planner is None
    assert calls == []


def test_http_maintenance_planner_still_uses_openrouter_when_query_planner_disabled(monkeypatch) -> None:
    settings = DorySettings(query_planner_enabled=False)

    def fake_build_openrouter_client(settings: DorySettings | None = None, *, purpose: str = "default"):
        del settings, purpose
        return _FakePlannerClient()

    monkeypatch.setattr(http_app, "build_openrouter_client", fake_build_openrouter_client)

    planner = http_app._build_retrieval_planner(settings, purpose="maintenance")

    assert planner is not None


def test_cli_query_planner_can_be_disabled(monkeypatch) -> None:
    settings = DorySettings(query_planner_enabled=False)
    calls: list[str] = []

    def fake_build_openrouter_client_for_purpose(settings: DorySettings, *, purpose: str):
        del settings
        calls.append(purpose)
        return _FakePlannerClient()

    monkeypatch.setattr(cli_main, "_build_openrouter_client_for_purpose", fake_build_openrouter_client_for_purpose)

    planner = cli_main._build_retrieval_planner(settings, purpose="query")

    assert planner is None
    assert calls == []


def test_query_reranker_can_be_disabled(monkeypatch) -> None:
    settings = DorySettings(query_reranker_enabled=False)
    calls: list[str] = []

    def fake_build_openrouter_client(settings: DorySettings | None = None, *, purpose: str = "default"):
        del settings
        calls.append(purpose)
        return _FakePlannerClient()

    monkeypatch.setattr("dory_core.llm_rerank.build_openrouter_client", fake_build_openrouter_client)

    reranker = build_reranker(settings)

    assert reranker is None
    assert calls == []
