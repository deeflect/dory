from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dory_core.artifacts import (
    ArtifactWriter,
    render_artifact,
    render_briefing_artifact,
    render_report_artifact,
    render_wiki_note_artifact,
    resolve_artifact_target,
)
from dory_core.errors import DoryValidationError
from dory_core.types import ArtifactReq


def test_resolve_artifact_target_routes_report() -> None:
    req = ArtifactReq(
        kind="report",
        title="Rooster focus status",
        question="What are we working on right now?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    target = resolve_artifact_target(req, created="2026-04-13")

    assert target == "references/reports/2026-04-13-rooster-focus-status.md"


def test_resolve_artifact_target_routes_briefing() -> None:
    req = ArtifactReq(
        kind="briefing",
        title="Rooster briefing",
        question="Summarize Rooster.",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    target = resolve_artifact_target(req, created="2026-04-13")

    assert target == "references/briefings/2026-04-13-rooster-briefing.md"


def test_render_report_artifact_includes_question_and_sources() -> None:
    req = ArtifactReq(
        kind="report",
        title="Rooster focus status",
        question="What are we working on right now?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md", "wiki/projects/rooster.md"],
    )

    rendered = render_report_artifact(req, created="2026-04-13")

    assert "type: report" in rendered
    assert "question: What are we working on right now?" in rendered
    assert "- core/active.md" in rendered
    assert "- wiki/projects/rooster.md" in rendered


def test_render_artifact_aliases_report_renderer() -> None:
    req = ArtifactReq(
        kind="report",
        title="Rooster focus status",
        question="What are we working on right now?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    assert render_artifact(req, created="2026-04-13") == render_report_artifact(
        req,
        created="2026-04-13",
    )


def test_render_briefing_artifact_uses_briefing_sections() -> None:
    req = ArtifactReq(
        kind="briefing",
        title="Rooster briefing",
        question="Summarize Rooster.",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    rendered = render_briefing_artifact(req, created="2026-04-13")

    assert "type: briefing" in rendered
    assert "## Briefing" in rendered
    assert "## Question" in rendered
    assert "## Findings" not in rendered


def test_render_wiki_note_artifact_uses_wiki_note_sections() -> None:
    req = ArtifactReq(
        kind="wiki-note",
        title="Rooster topic",
        question="What do we know about Rooster?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    rendered = render_wiki_note_artifact(req, created="2026-04-13")

    assert "type: wiki-note" in rendered
    assert "## Summary" in rendered
    assert "## Notes" in rendered


def test_artifact_writer_persists_markdown(tmp_path: Path) -> None:
    req = ArtifactReq(
        kind="wiki-note",
        title="Rooster topic",
        question="What do we know about Rooster?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    resp = ArtifactWriter(tmp_path).write(req, created="2026-04-13")

    assert resp.path == "wiki/concepts/rooster-topic.md"
    written = (tmp_path / resp.path).read_text(encoding="utf-8")
    assert "# Rooster topic" in written
    assert "## Summary" in written
    assert "## Notes" in written


def test_artifact_writer_triggers_reindex_when_index_wired(
    tmp_path: Path, fake_embedder
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()

    req = ArtifactReq(
        kind="report",
        title="Rooster focus status",
        question="What are we working on right now?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    resp = ArtifactWriter(
        corpus_root,
        index_root=index_root,
        embedder=fake_embedder,
    ).write(req, created="2026-04-13")

    with sqlite3.connect(index_root / "dory.db") as connection:
        rows = connection.execute(
            "SELECT path FROM chunks WHERE path = ?",
            (resp.path,),
        ).fetchall()

    assert rows, "expected artifact path to be indexed after write"


def test_artifact_writer_skips_reindex_when_unwired(tmp_path: Path) -> None:
    req = ArtifactReq(
        kind="report",
        title="Rooster focus status",
        question="What are we working on right now?",
        body="Rooster is the active focus this week.",
        sources=["core/active.md"],
    )

    ArtifactWriter(tmp_path).write(req, created="2026-04-13")

    assert not (tmp_path / "dory.db").exists()


@pytest.mark.parametrize("target", ["../outside.md", "/tmp/outside.md", "references/report.txt"])
def test_artifact_writer_rejects_unsafe_targets(tmp_path: Path, target: str) -> None:
    req = ArtifactReq(
        kind="report",
        title="Unsafe target",
        question="Can this escape?",
        body="No.",
        sources=[],
        target=target,
    )

    with pytest.raises(DoryValidationError):
        ArtifactWriter(tmp_path / "corpus").write(req, created="2026-04-13")

    assert not (tmp_path / "outside.md").exists()
