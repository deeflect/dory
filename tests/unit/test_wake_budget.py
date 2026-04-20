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
    resp = WakeBuilder(sample_corpus_root).build(
        WakeReq(agent="codex", budget_tokens=200, include_recent_sessions=1)
    )

    assert "## Recent sessions" in resp.block
    assert "logs/sessions/claude-code/2026-04-07.md" in resp.block
    assert resp.sources[-1] == "logs/sessions/claude-code/2026-04-07.md"


def test_wake_builder_includes_decisions_when_requested(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(
        WakeReq(agent="claude-code", budget_tokens=400, include_pinned_decisions=True)
    )

    assert "HomeServer daemon host" in resp.block
    assert "decisions/2026-04-07-homeserver.md" in resp.sources


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
        "---\n"
        "title: Older\n"
        "---\n"
        "# Session\n\n"
        "Older focus note.\n",
        encoding="utf-8",
    )
    newer.write_text(
        "---\n"
        "title: Newer\n"
        "---\n"
        "# Session\n\n"
        "Newest focus note.\n",
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
