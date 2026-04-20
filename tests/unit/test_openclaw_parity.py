from __future__ import annotations

import sqlite3
from pathlib import Path

from dory_core.frontmatter import dump_markdown_document
from dory_core.openclaw_parity import OpenClawParityStore, list_public_artifacts
from dory_core.types import RecallEventReq


def test_record_recall_event_persists_selected_path(tmp_path: Path) -> None:
    store = OpenClawParityStore(index_root=tmp_path / ".index")
    req = RecallEventReq(
        agent="openclaw",
        session_key="sess-1",
        query="who is anna",
        result_paths=["people/anna.md", "core/user.md"],
        selected_path="people/anna.md",
        corpus="memory",
        source="openclaw-recall",
    )

    resp = store.record_recall_event(req)

    assert resp.stored is True
    assert resp.selected_path == "people/anna.md"
    assert resp.created_at is not None

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            """
            SELECT agent, session_key, query, result_paths_json, selected_path, corpus, source
            FROM openclaw_recall_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row == (
        "openclaw",
        "sess-1",
        "who is anna",
        '["people/anna.md", "core/user.md"]',
        "people/anna.md",
        "memory",
        "openclaw-recall",
    )

    recent = store.load_recent_recall_events(limit=1)
    assert len(recent) == 1
    assert recent[0].selected_path == "people/anna.md"
    assert recent[0].result_paths == ("people/anna.md", "core/user.md")
    assert recent[0].created_at.endswith("Z")

    diagnostics = store.diagnostics()
    assert diagnostics.flush_enabled is False
    assert diagnostics.recall_tracking_enabled is True
    assert diagnostics.artifact_listing_enabled is True
    assert diagnostics.recent_recall_count == 1
    assert diagnostics.last_recall_selected_path == "people/anna.md"


def test_recall_promotion_candidates_and_marks_are_persisted(tmp_path: Path) -> None:
    store = OpenClawParityStore(index_root=tmp_path / ".index")
    for query in ("who is anna", "anna prefs", "anna status"):
        store.record_recall_event(
            RecallEventReq(
                agent="openclaw",
                session_key="sess-2",
                query=query,
                result_paths=["people/anna.md"],
                selected_path="people/anna.md",
                corpus="memory",
                source="openclaw-recall",
            )
        )

    candidates = store.list_recall_promotion_candidates(min_events=2)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.selected_path == "people/anna.md"
    assert candidate.event_count == 3
    assert candidate.query_count == 3
    assert "who is anna" in candidate.sample_queries

    store.mark_recall_promotion(candidate=candidate, distilled_path="inbox/distilled/recall-people-anna.md")

    assert store.list_recall_promotion_candidates(min_events=2) == ()
    diagnostics = store.diagnostics()
    assert diagnostics.promotion_candidate_count == 0
    assert diagnostics.last_recall_promotion_at is not None


def test_readonly_parity_store_does_not_create_database(tmp_path: Path) -> None:
    index_root = tmp_path / ".index"

    diagnostics = OpenClawParityStore(index_root=index_root, readonly=True).diagnostics()

    assert diagnostics.flush_enabled is False
    assert diagnostics.recall_tracking_enabled is True
    assert diagnostics.artifact_listing_enabled is True
    assert not (index_root / "dory.db").exists()


def test_list_public_artifacts_returns_expected_paths_and_metadata(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "references" / "reports" / "migrations").mkdir(parents=True)
    (corpus_root / "references" / "briefings").mkdir(parents=True)
    (corpus_root / "wiki" / "projects").mkdir(parents=True)
    (corpus_root / "logs").mkdir(parents=True)

    (corpus_root / "core" / "user.md").write_text(
        dump_markdown_document(
            {"title": "User", "agent_ids": ["openclaw"]},
            "# User\n",
        ),
        encoding="utf-8",
    )
    (corpus_root / "references" / "reports" / "migrations" / "2026-04-13-rooster.md").write_text(
        dump_markdown_document(
            {"title": "Rooster Migration", "agent": "openclaw"},
            "Migration report.\n",
        ),
        encoding="utf-8",
    )
    (corpus_root / "references" / "briefings" / "2026-04-13-rooster.md").write_text(
        dump_markdown_document(
            {"title": "Rooster Briefing"},
            "Briefing.\n",
        ),
        encoding="utf-8",
    )
    (corpus_root / "wiki" / "projects" / "rooster.md").write_text(
        dump_markdown_document(
            {"title": "Rooster Wiki"},
            "Wiki.\n",
        ),
        encoding="utf-8",
    )
    (corpus_root / "logs" / "daily.md").write_text("no frontmatter here\n", encoding="utf-8")

    artifacts = list_public_artifacts(corpus_root)

    assert [artifact.relative_path for artifact in artifacts] == [
        "core/user.md",
        "references/briefings/2026-04-13-rooster.md",
        "references/reports/migrations/2026-04-13-rooster.md",
        "wiki/projects/rooster.md",
    ]
    assert artifacts[0].kind == "core"
    assert artifacts[0].title == "User"
    assert artifacts[0].agent_ids == ["openclaw"]
    assert artifacts[1].kind == "briefing"
    assert artifacts[2].kind == "report"
    assert artifacts[3].kind == "wiki"
