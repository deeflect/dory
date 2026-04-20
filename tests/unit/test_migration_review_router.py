from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dory_core.llm.openrouter import OpenRouterProviderError
from dory_core.migration_review_router import OpenRouterReviewRouter
from dory_core.migration_source_router import RoutingDecision


@dataclass
class _FakeClient:
    payload: Any = None
    raise_error: bool = False

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict,
    ) -> Any:
        if self.raise_error:
            raise OpenRouterProviderError("boom")
        return self.payload


def test_review_router_upgrades_decision_to_route(tmp_path: Path) -> None:
    source = tmp_path / "2026-04-16-memory-check.md"
    source.write_text(
        "---\ntitle: memory check\n---\n\nQuick sanity check of recall accuracy.\n",
        encoding="utf-8",
    )
    decision = RoutingDecision.review(source, reason="root-level dated file")
    client = _FakeClient(payload={
        "bucket": "logs/daily",
        "filename_hint": None,
        "reason": "the file reads as a daily memory audit on 2026-04-16",
    })
    router = OpenRouterReviewRouter(client=client)  # type: ignore[arg-type]

    upgraded = router.resolve(decision)

    assert upgraded.kind == "route"
    assert upgraded.destination == Path("logs/daily/2026-04-16.md")
    assert "llm-routed" in upgraded.tags


def test_review_router_keeps_decision_on_provider_error(tmp_path: Path) -> None:
    source = tmp_path / "foo.md"
    source.write_text("---\ntitle: foo\n---\n\nbody\n", encoding="utf-8")
    decision = RoutingDecision.review(source, reason="ambiguous")
    client = _FakeClient(raise_error=True)
    router = OpenRouterReviewRouter(client=client)  # type: ignore[arg-type]

    result = router.resolve(decision)

    assert result is decision


def test_review_router_keeps_decision_when_bucket_invalid(tmp_path: Path) -> None:
    source = tmp_path / "foo.md"
    source.write_text("---\ntitle: foo\n---\n\nbody\n", encoding="utf-8")
    decision = RoutingDecision.review(source, reason="ambiguous")
    client = _FakeClient(payload={
        "bucket": "nonsense",
        "filename_hint": None,
        "reason": "whatever",
    })
    router = OpenRouterReviewRouter(client=client)  # type: ignore[arg-type]

    result = router.resolve(decision)

    assert result is decision


def test_review_router_respects_filename_hint(tmp_path: Path) -> None:
    source = tmp_path / "odd-name.md"
    source.write_text("---\ntitle: odd\n---\n\nbody\n", encoding="utf-8")
    decision = RoutingDecision.review(source, reason="ambiguous")
    client = _FakeClient(payload={
        "bucket": "references/reports",
        "filename_hint": "weekly sync",
        "reason": "reads as a meeting sync summary",
    })
    router = OpenRouterReviewRouter(client=client)  # type: ignore[arg-type]

    result = router.resolve(decision)

    assert result.kind == "route"
    assert result.destination == Path("references/reports/weekly-sync.md")


def test_review_router_pass_through_for_non_review_decisions(tmp_path: Path) -> None:
    source = tmp_path / "foo.md"
    source.write_text("x", encoding="utf-8")
    decision = RoutingDecision.route(source, Path("inbox/foo.md"), reason="ok")
    client = _FakeClient(payload={"bucket": "projects", "filename_hint": None, "reason": "x"})
    router = OpenRouterReviewRouter(client=client)  # type: ignore[arg-type]

    result = router.resolve(decision)

    assert result is decision
