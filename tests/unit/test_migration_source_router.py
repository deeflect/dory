from __future__ import annotations

from pathlib import Path

from dory_core.migration_source_router import (
    RoutingDecision,
    route_source_path,
    walk_source_tree,
)


def _make(source_root: Path, relative: str, *, content: str = "# x\n") -> Path:
    path = source_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _route(source_root: Path, relative: str) -> RoutingDecision:
    source = _make(source_root, relative)
    return route_source_path(source, source_root=source_root)


def test_active_daily_narrative_routes_to_logs_daily(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/daily/2026-04-10.md")

    assert decision.kind == "route"
    assert decision.destination == Path("logs/daily/2026-04-10.md")
    assert "daily" in decision.tags


def test_active_daily_digest_routes_to_digests_daily(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/daily/2026-04-01-digest.md")

    assert decision.kind == "route"
    assert decision.destination == Path("digests/daily/2026-04-01.md")
    assert set(decision.tags) >= {"digest", "daily"}


def test_active_weekly_routes_to_logs_weekly(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/weekly/2026-W14.md")

    assert decision.kind == "route"
    assert decision.destination == Path("logs/weekly/2026-W14.md")


def test_active_session_routes_to_logs_sessions(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/sessions/2026-04-15-flashmoe.md")

    assert decision.kind == "route"
    assert decision.destination == Path("logs/sessions/2026-04-15-flashmoe.md")


def test_active_project_top_level_becomes_state_file(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/projects/borb-bot.md")

    assert decision.kind == "route"
    assert decision.destination == Path("projects/borb-bot/state.md")


def test_active_project_subdir_readme_becomes_state(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/projects/x-growth-system/README.md")

    assert decision.kind == "route"
    assert decision.destination == Path("projects/x-growth-system/state.md")


def test_active_project_subdir_file_preserved(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/projects/x-growth-system/tweet-drafts-new.md")

    assert decision.kind == "route"
    assert decision.destination == Path("projects/x-growth-system/tweet-drafts-new.md")


def test_active_project_supporting_flat_routes_to_supporting(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/projects/_supporting/borb-bot.md")

    assert decision.kind == "route"
    assert decision.destination == Path("projects/borb-bot/supporting.md")
    assert "supporting" in decision.tags


def test_active_idea_routes_to_ideas(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/ideas/2026-02-19-casey-ceo.md")

    assert decision.kind == "route"
    assert decision.destination == Path("ideas/2026-02-19-casey-ceo.md")


def test_active_decision_routes_to_decisions(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/decisions/2026-04-gpt54-migration.md")

    assert decision.kind == "route"
    assert decision.destination == Path("decisions/2026-04-gpt54-migration.md")


def test_active_person_routes_to_people(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/people/casey.md")

    assert decision.kind == "route"
    assert decision.destination == Path("people/casey.md")


def test_active_analysis_routes_to_reports(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/analysis/2026-04-16-work-pattern-analysis.md")

    assert decision.kind == "route"
    assert decision.destination == Path(
        "references/reports/2026-04-16-work-pattern-analysis.md"
    )


def test_active_strategy_routes_to_reports(tmp_path: Path) -> None:
    decision = _route(tmp_path, "active/strategy/2026-04-16-income-strategy.md")

    assert decision.kind == "route"
    assert decision.destination == Path(
        "references/reports/2026-04-16-income-strategy.md"
    )


def test_reference_knowledge_preserves_subtree(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/knowledge/ai/graph-summary.md")

    assert decision.kind == "route"
    assert decision.destination == Path("knowledge/ai/graph-summary.md")


def test_reference_tools_routes_to_knowledge_tools(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/tools/openrouter.md")

    assert decision.kind == "route"
    assert decision.destination == Path("knowledge/tools/openrouter.md")


def test_reference_health_routes_to_knowledge_health(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/health/hrv.md")

    assert decision.kind == "route"
    assert decision.destination == Path("knowledge/health/hrv.md")


def test_reference_resources_routes_to_references_notes(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/resources/fitness-app-competitors.md")

    assert decision.kind == "route"
    assert decision.destination == Path(
        "references/notes/fitness-app-competitors.md"
    )


def test_reference_tweets_routes_to_references_tweets(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/tweets/2026-03-01.md")

    assert decision.kind == "route"
    assert decision.destination == Path("references/tweets/2026-03-01.md")


def test_reference_drafts_routes_to_inbox_drafts(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/drafts/blog-post.md")

    assert decision.kind == "route"
    assert decision.destination == Path("inbox/drafts/blog-post.md")


def test_reference_supporting_flat_file_needs_review(tmp_path: Path) -> None:
    decision = _route(tmp_path, "reference/supporting/some-notes.md")

    assert decision.kind == "review"
    assert "supporting" in decision.tags


def test_reference_supporting_unknown_subpurpose_preserved(tmp_path: Path) -> None:
    decision = _route(
        tmp_path, "reference/supporting/ad-hoc/some-notes.md"
    )

    assert decision.kind == "route"
    assert decision.destination == Path("references/supporting/ad-hoc/some-notes.md")


def test_reference_supporting_project_architecture_routes_to_project(
    tmp_path: Path,
) -> None:
    decision = _route(
        tmp_path,
        "reference/supporting/project-architecture/burnrate-architecture.md",
    )

    assert decision.kind == "route"
    assert decision.destination == Path("projects/burnrate/architecture.md")


def test_reference_supporting_project_briefs_routes_to_project(
    tmp_path: Path,
) -> None:
    decision = _route(
        tmp_path,
        "reference/supporting/project-briefs/casey-ceo-master-brief.md",
    )

    assert decision.kind == "route"
    assert decision.destination == Path("projects/casey-ceo/brief.md")


def test_reference_supporting_project_strategy_routes_to_project(
    tmp_path: Path,
) -> None:
    decision = _route(
        tmp_path,
        "reference/supporting/project-strategy/atlasapp-strategy.md",
    )

    assert decision.kind == "route"
    assert decision.destination == Path("projects/atlasapp/strategy.md")


def test_reference_supporting_generated_output_routes_to_reports(
    tmp_path: Path,
) -> None:
    decision = _route(
        tmp_path,
        "reference/supporting/generated-output/book-production-pipeline.md",
    )

    assert decision.kind == "route"
    assert decision.destination == Path(
        "references/reports/generated/book-production-pipeline.md"
    )


def test_reference_supporting_graphs_routes_to_knowledge_graphs(
    tmp_path: Path,
) -> None:
    decision = _route(tmp_path, "reference/supporting/graphs/graph-summary.md")

    assert decision.kind == "route"
    assert decision.destination == Path("knowledge/graphs/graph-summary.md")


def test_archive_preserved(tmp_path: Path) -> None:
    decision = _route(tmp_path, "archive/projects/casey-ink.md")

    assert decision.kind == "route"
    assert decision.destination == Path("archive/projects/casey-ink.md")
    assert "legacy" in decision.tags


def test_archive_daily_preserved(tmp_path: Path) -> None:
    decision = _route(tmp_path, "archive/daily/2026-01-31-digest.md")

    assert decision.kind == "route"
    assert decision.destination == Path("archive/daily/2026-01-31-digest.md")


def test_system_directory_excluded(tmp_path: Path) -> None:
    decision = _route(tmp_path, "system/dreams/short-term-recall.json")

    assert decision.kind == "exclude"


def test_media_directory_excluded(tmp_path: Path) -> None:
    decision = _route(tmp_path, "media/images/borb.png")

    assert decision.kind == "exclude"


def test_non_markdown_excluded(tmp_path: Path) -> None:
    path = _make(tmp_path, "heartbeat-state.json", content="{}\n")
    decision = route_source_path(path, source_root=tmp_path)

    assert decision.kind == "exclude"
    assert "non-markdown" in decision.reason


def test_ops_reports_routed_under_references(tmp_path: Path) -> None:
    decision = _route(tmp_path, "ops/reports/audit/memory-audit-2026-04-16.md")

    assert decision.kind == "route"
    assert decision.destination == Path(
        "references/reports/ops/reports/audit/memory-audit-2026-04-16.md"
    )


def test_inbox_preserved(tmp_path: Path) -> None:
    decision = _route(tmp_path, "inbox/overnight-research/wave-1.md")

    assert decision.kind == "route"
    assert decision.destination == Path("inbox/overnight-research/wave-1.md")


def test_root_dated_file_routes_to_daily(tmp_path: Path) -> None:
    decision = _route(tmp_path, "2026-04-16.md")

    assert decision.kind == "route"
    assert decision.destination == Path("logs/daily/2026-04-16.md")


def test_root_dated_digest_file_routes_to_digests(tmp_path: Path) -> None:
    decision = _route(tmp_path, "2026-04-16-memory-check.md")

    assert decision.kind == "review"
    assert "dated-root" in decision.tags


def test_walk_source_tree_produces_decisions(tmp_path: Path) -> None:
    _make(tmp_path, "active/daily/2026-04-10.md")
    _make(tmp_path, "active/projects/foo.md")
    _make(tmp_path, "media/a.png", content="binary")
    _make(tmp_path, "system/dreams/x.md")
    _make(tmp_path, "reference/knowledge/ai/graph-summary.md")
    _make(tmp_path, ".stfolder/placeholder", content="x")

    decisions = walk_source_tree(tmp_path)

    by_kind: dict[str, int] = {}
    for decision in decisions:
        by_kind[decision.kind] = by_kind.get(decision.kind, 0) + 1

    kinds = {d.kind for d in decisions}
    assert "route" in kinds
    assert "exclude" in kinds
    destinations = {d.destination for d in decisions if d.kind == "route"}
    assert Path("logs/daily/2026-04-10.md") in destinations
    assert Path("projects/foo/state.md") in destinations
    assert Path("knowledge/ai/graph-summary.md") in destinations
