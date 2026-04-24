from __future__ import annotations

import os
from pathlib import Path

from dory_core.types import WakeReq
from dory_core.wake import WakeBuilder


class _FakeTokenCounter:
    def count(self, text: str, *, agent: str = "default") -> int:
        del agent
        if "Soul" in text:
            return 100
        return 5


def test_wake_builder_prioritizes_user_soul_env_before_active(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=180))

    assert "User" in resp.block
    assert "Soul" in resp.block
    assert "Environment" in resp.block
    assert "Active Work" in resp.block
    assert resp.sources[:4] == [
        "core/user.md",
        "core/soul.md",
        "core/env.md",
        "core/active.md",
    ]


def test_wake_builder_coding_profile_prioritizes_operational_context(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=180, include_recent_sessions=0)
    )

    assert resp.profile == "coding"
    assert "Active Work" in resp.block
    assert "Environment" in resp.block
    assert "User" not in resp.block
    assert "Soul" not in resp.block
    assert resp.sources[:2] == [
        "core/active.md",
        "core/env.md",
    ]


def test_wake_builder_truncates_when_budget_is_tight(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=12))

    assert "User" in resp.block
    assert "Soul" not in resp.block
    assert resp.sources == ["core/user.md"]


def test_wake_builder_includes_recent_sessions_when_budget_allows(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="codex", budget_tokens=200, include_recent_sessions=1))

    assert "## Recent sessions" in resp.block
    assert "logs/sessions/claude-code/2026-04-07.md" in resp.block
    assert resp.sources[-1] == "logs/sessions/claude-code/2026-04-07.md"


def test_wake_builder_includes_decisions_when_requested(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(
        WakeReq(agent="claude-code", budget_tokens=400, include_pinned_decisions=True)
    )

    assert "HomeServer daemon host" in resp.block
    assert "decisions/2026-04-07-homeserver.md" in resp.sources


def test_wake_builder_includes_project_context_by_slug(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "dory").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "projects" / "dory" / "state.md").write_text(
        "---\ntitle: Dory\ntype: project\n---\n\n## Summary\n- Dory project context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, project="dory")
    )

    assert "Dory project context." in resp.block
    assert "projects/dory/state.md" in resp.sources


def test_wake_builder_resolves_project_context_by_title_or_alias(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "palace").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "projects" / "palace" / "state.md").write_text(
        """---
title: Dory
type: project
slug: palace
aliases:
- Dory memory
---

## Summary
- Alias-routed project context.
""",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, project="Dory memory")
    )

    assert "Alias-routed project context." in resp.block
    assert "projects/palace/state.md" in resp.sources


def test_wake_builder_skips_unpinned_decisions(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "decisions").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "decisions" / "unpinned.md").write_text(
        "---\ntitle: Unpinned\ntype: decision\nstatus: active\n---\n\nShould stay out of wake.\n",
        encoding="utf-8",
    )
    (tmp_path / "decisions" / "pinned.md").write_text(
        "---\ntitle: Pinned\ntype: decision\nstatus: active\npinned: true\n---\n\nShould appear.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="claude-code", budget_tokens=400, include_recent_sessions=0, include_pinned_decisions=True)
    )

    assert "Should appear." in resp.block
    assert "Should stay out of wake." not in resp.block
    assert "decisions/pinned.md" in resp.sources
    assert "decisions/unpinned.md" not in resp.sources


def test_wake_builder_uses_token_counter_for_budgeting(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root, token_counter=_FakeTokenCounter()).build(
        WakeReq(agent="claude-code", budget_tokens=20, include_recent_sessions=0)
    )

    assert "User" in resp.block
    assert "Soul" not in resp.block
    assert resp.sources == ["core/user.md"]


def test_wake_builder_sorts_recent_sessions_by_mtime_and_skips_heading_lines(tmp_path: Path) -> None:
    sessions_root = tmp_path / "logs" / "sessions" / "claude"
    sessions_root.mkdir(parents=True)
    older = sessions_root / "2026-04-10.md"
    newer = sessions_root / "2026-04-01.md"
    older.write_text(
        "---\ntitle: Older\n---\n# Session\n\nOlder focus note.\n",
        encoding="utf-8",
    )
    newer.write_text(
        "---\ntitle: Newer\n---\n# Session\n\nNewest focus note.\n",
        encoding="utf-8",
    )
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="claude-code", budget_tokens=200, include_recent_sessions=1, include_pinned_decisions=False)
    )

    assert "## Recent sessions" in resp.block
    assert "logs/sessions/claude/2026-04-01.md: Newest focus note." in resp.block
    assert "logs/sessions/claude/2026-04-10.md" not in resp.block


def test_wake_builder_writing_profile_uses_voice_file_without_full_identity(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "knowledge" / "personal").mkdir(parents=True)
    (tmp_path / "core" / "soul.md").write_text("# Soul\n\nUse direct short sentences.\n", encoding="utf-8")
    (tmp_path / "core" / "user.md").write_text("# User\n\nEmail placeholder@example.invalid.\n", encoding="utf-8")
    (tmp_path / "core" / "identity.md").write_text("# Identity\n\nBirthday 1900-01-01.\n", encoding="utf-8")
    (tmp_path / "knowledge" / "personal" / "dee-writing-voice.md").write_text(
        "# Writing Voice\n\nLowercase by default. No AI buzzwords.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="writing", budget_tokens=400, include_recent_sessions=0)
    )

    assert "Use direct short sentences." in resp.block
    assert "No AI buzzwords." in resp.block
    assert "placeholder@example.invalid" not in resp.block
    assert "Birthday" not in resp.block
    assert "core/user.md" not in resp.sources
    assert "core/identity.md" not in resp.sources


def test_wake_builder_privacy_profile_extracts_boundaries_not_identifiers(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "user.md").write_text(
        """---
title: User
---

# User

Email placeholder@example.invalid.
Birthday 1900-01-01.

## Privacy Boundaries
- Sensitive category alpha is private.
- Sensitive category beta is private.
- Do not share placeholder identifiers.
""",
        encoding="utf-8",
    )
    (tmp_path / "core" / "identity.md").write_text(
        "# Identity\n\nEmail identity-placeholder@example.invalid.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="privacy", budget_tokens=400, include_recent_sessions=0)
    )

    assert "Privacy Boundaries" in resp.block
    assert "Sensitive category alpha is private." in resp.block
    assert "Sensitive category beta is private." in resp.block
    assert "placeholder@example.invalid" not in resp.block
    assert "Birthday" not in resp.block
    assert "identity-placeholder@example.invalid" not in resp.block
    assert "core/identity.md" not in resp.sources
