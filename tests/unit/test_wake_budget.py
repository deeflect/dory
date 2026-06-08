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


def test_wake_builder_project_scope_uses_project_state_instead_of_global_active(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "supplements").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nGlobal Dory cleanup context.\n", encoding="utf-8")
    (tmp_path / "core" / "env.md").write_text("# Env\n\nRuntime context.\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("# Defaults\n\nDefault tools.\n", encoding="utf-8")
    (tmp_path / "projects" / "supplements" / "state.md").write_text(
        "---\ntitle: Supplements\ntype: project\n---\n\n## Summary\n- Supplement project current truth.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(
            agent="codex",
            profile="coding",
            project="supplements",
            budget_tokens=400,
            include_recent_sessions=0,
        )
    )

    assert "Supplement project current truth." in resp.block
    assert "Global Dory cleanup context." not in resp.block
    assert "core/active.md" not in resp.sources
    assert resp.sources == ["projects/supplements/state.md", "core/env.md", "core/defaults.md"]


def test_wake_builder_truncates_when_budget_is_tight(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=12))

    assert "User" in resp.block
    assert "Soul" not in resp.block
    assert resp.sources == ["core/user.md"]


def test_wake_builder_suppresses_duplicate_profile_sections(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nUnique wake marker.\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("# Defaults\n\nDefault instruction.\n", encoding="utf-8")
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  duplicate:
    wake:
      sections:
        - core/active.md
        - active
        - core/defaults.md
""".strip(),
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="duplicate", budget_tokens=300, include_pinned_decisions=False)
    )

    assert resp.block.count("Unique wake marker.") == 1
    assert resp.sources == ["core/active.md", "core/defaults.md"]


def test_wake_builder_applies_source_policy_to_profile_sections(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir(parents=True)
    (tmp_path / "inbox").mkdir(parents=True)
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  policy-test:
    wake:
      sections:
        - notes/warm.md
        - notes/stale.md
        - notes/cold.md
        - notes/private.md
        - notes/generated.md
        - inbox/raw.md
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "notes" / "warm.md").write_text(
        "---\ntitle: Warm\ntemperature: warm\nstatus: active\n---\n\nWarm wake context.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "stale.md").write_text(
        "---\ntitle: Stale\nstatus: stale\n---\n\nStale wake context.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "cold.md").write_text(
        "---\ntitle: Cold\ntemperature: cold\n---\n\nCold wake context.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "private.md").write_text(
        "---\ntitle: Private\nvisibility: private\n---\n\nPrivate wake context.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "generated.md").write_text(
        "---\ntitle: Generated\nsource_kind: generated\n---\n\nGenerated wake context.\n",
        encoding="utf-8",
    )
    (tmp_path / "inbox" / "raw.md").write_text(
        "---\ntitle: Raw\nstatus: active\n---\n\nInbox wake context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="policy-test", budget_tokens=800, include_pinned_decisions=False)
    )

    assert "Warm wake context." in resp.block
    assert "Stale wake context." not in resp.block
    assert "Cold wake context." not in resp.block
    assert "Private wake context." in resp.block
    assert "Generated wake context." not in resp.block
    assert "Inbox wake context." not in resp.block
    assert resp.sources == ["notes/warm.md", "notes/private.md"]
    assert resp.warnings == [
        "Wake source skipped notes/stale.md: status 'stale' is not wake-eligible.",
        "Wake source skipped notes/cold.md: cold memory is not wake-eligible.",
        "Wake source skipped notes/generated.md: generated non-wiki memory is not wake-eligible.",
        "Wake source skipped inbox/raw.md: inbox and archive files are not wake-eligible.",
    ]


def test_wake_builder_coding_profile_skips_private_project_state(tmp_path: Path) -> None:
    (tmp_path / "projects" / "dory").mkdir(parents=True)
    (tmp_path / "projects" / "dory" / "state.md").write_text(
        "---\ntitle: Dory\ntype: project\nvisibility: private\nsensitivity: personal\n---\n\nPrivate project state.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", project="dory", budget_tokens=400, include_recent_sessions=0)
    )

    assert resp.block == ""
    assert resp.sources == []
    assert resp.warnings == [
        "Wake source skipped projects/dory/state.md: profile 'coding' does not load private files at wake."
    ]


def test_wake_builder_includes_only_completed_recent_sessions_when_budget_allows(tmp_path: Path) -> None:
    sessions_root = tmp_path / "logs" / "sessions" / "codex"
    sessions_root.mkdir(parents=True)
    active = sessions_root / "2026-04-08-active.md"
    done = sessions_root / "2026-04-07-done.md"
    active.write_text(
        "---\ntitle: Active\ntype: session\nstatus: active\n---\n\nActive in-progress note.\n",
        encoding="utf-8",
    )
    done.write_text(
        "---\ntitle: Done\ntype: session\nstatus: done\n---\n\nCompleted handoff note.\n",
        encoding="utf-8",
    )
    os.utime(active, (200, 200))
    os.utime(done, (100, 100))

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", budget_tokens=200, include_recent_sessions=1, include_pinned_decisions=False)
    )

    assert "## Recent sessions" in resp.block
    assert "logs/sessions/codex/2026-04-07-done.md" in resp.block
    assert "Completed handoff note." in resp.block
    assert "logs/sessions/codex/2026-04-08-active.md" not in resp.block
    assert resp.sources[-1] == "logs/sessions/codex/2026-04-07-done.md"


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
    assert resp.sources[0] == "projects/dory/state.md"


def test_wake_builder_infers_project_context_from_cwd_manifest(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    workspace = tmp_path / "workspace"
    nested = workspace / "src" / "feature"
    nested.mkdir(parents=True)
    (workspace / "pyproject.toml").write_text('[project]\nname = "dory"\n', encoding="utf-8")
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text("# Active\n\nGlobal Dory ops.\n", encoding="utf-8")
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        "---\ntitle: Dory\ntype: project\n---\n\n## Summary\n- Cwd-routed project context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(corpus_root).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, cwd=str(nested))
    )

    assert "Cwd-routed project context." in resp.block
    assert resp.sources[0] == "projects/dory/state.md"


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


def test_wake_builder_uses_canonical_project_slug_after_cwd_alias_resolution(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    workspace = tmp_path / "workspace" / "mies"
    workspace.mkdir(parents=True)
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "projects" / "palace").mkdir(parents=True)
    (corpus_root / "wiki" / "projects").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text("# Active\n\nGlobal context.\n", encoding="utf-8")
    (corpus_root / "projects" / "palace" / "state.md").write_text(
        """---
title: Palace
type: project
slug: palace
workspace_aliases:
- mies
---

## Summary
- Alias-routed raw project state.
""",
        encoding="utf-8",
    )
    (corpus_root / "wiki" / "projects" / "palace.md").write_text(
        "---\ntitle: Palace\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: warm\nsource_kind: generated\n---\n\n# Palace\n\n- Alias-routed compiled project card.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(corpus_root).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=800, include_recent_sessions=0, cwd=str(workspace))
    )

    assert "Alias-routed compiled project card." in resp.block
    assert "Alias-routed raw project state." in resp.block
    assert resp.sources[:2] == ["wiki/projects/palace.md", "projects/palace/state.md"]


def test_wake_builder_resolves_fuzzy_project_handle(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "agent-mission-control").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nGlobal context.\n", encoding="utf-8")
    (tmp_path / "projects" / "agent-mission-control" / "state.md").write_text(
        "---\ntitle: Agent Mission Control\ntype: project\n---\n\n## Summary\n- Fuzzy-routed project context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, project="mission control")
    )

    assert "Fuzzy-routed project context." in resp.block
    assert resp.sources[0] == "projects/agent-mission-control/state.md"


def test_wake_builder_ignores_ambiguous_fuzzy_project_handle(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "agent-control").mkdir(parents=True)
    (tmp_path / "projects" / "mission-control").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nGlobal context.\n", encoding="utf-8")
    (tmp_path / "projects" / "agent-control" / "state.md").write_text(
        "---\ntitle: Agent Control\ntype: project\n---\n\n## Summary\n- First fuzzy context.\n",
        encoding="utf-8",
    )
    (tmp_path / "projects" / "mission-control" / "state.md").write_text(
        "---\ntitle: Mission Control\ntype: project\n---\n\n## Summary\n- Second fuzzy context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, project="control")
    )

    assert "First fuzzy context." not in resp.block
    assert "Second fuzzy context." not in resp.block
    assert "Global context." not in resp.block
    assert "core/active.md" not in resp.sources
    assert resp.warnings == [
        "Project or cwd did not resolve to a known project; coding wake skipped global active context."
    ]


def test_wake_builder_coding_profile_skips_global_active_for_unresolved_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace" / "unknown-project"
    workspace.mkdir(parents=True)
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nUnrelated global project state.\n", encoding="utf-8")
    (tmp_path / "core" / "env.md").write_text("# Env\n\nAgent runtime context.\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("# Defaults\n\nRetrieve before claims.\n", encoding="utf-8")

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0, cwd=str(workspace))
    )

    assert "Unrelated global project state." not in resp.block
    assert "Agent runtime context." in resp.block
    assert "Retrieve before claims." in resp.block
    assert "core/active.md" not in resp.sources
    assert resp.sources == ["core/env.md", "core/defaults.md"]
    assert resp.warnings == [
        "Project or cwd did not resolve to a known project; coding wake skipped global active context."
    ]


def test_wake_builder_resolves_project_from_relative_path_handle(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "projects" / "atlas").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text("# Active\n\nGlobal context.\n", encoding="utf-8")
    (corpus_root / "projects" / "atlas" / "state.md").write_text(
        "---\ntitle: Atlas\ntype: project\n---\n\n## Summary\n- Relative-path project context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(corpus_root).build(
        WakeReq(
            agent="codex",
            profile="coding",
            budget_tokens=400,
            include_recent_sessions=0,
            project="apps/atlas",
            cwd=str(workspace),
        )
    )

    assert "Relative-path project context." in resp.block
    assert resp.sources[0] == "projects/atlas/state.md"


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
    (tmp_path / "knowledge" / "personal" / "writing-voice.md").write_text(
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


def test_wake_builder_includes_compiled_project_card_before_raw_state(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "palace").mkdir(parents=True)
    (tmp_path / "wiki" / "projects").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "projects" / "palace" / "state.md").write_text(
        "---\ntitle: Palace\ntype: project\n---\n\n## Summary\n- Raw project state content.\n",
        encoding="utf-8",
    )
    # Compiled project card (active + canonical + warm)
    (tmp_path / "wiki" / "projects" / "palace.md").write_text(
        "---\ntitle: Palace\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: warm\nsource_kind: generated\n---\n\n# Palace\n\n## Summary\n- Compiled project card content.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=800, include_recent_sessions=0, project="palace")
    )

    assert "Compiled project card content." in resp.block
    assert "Raw project state content." in resp.block
    # Compiled project card should appear before raw project state
    compiled_pos = resp.block.index("Compiled project card content.")
    raw_pos = resp.block.index("Raw project state content.")
    assert compiled_pos < raw_pos, "Compiled project card must appear before raw project state"
    # Both should be in sources
    assert "wiki/projects/palace.md" in resp.sources
    assert "projects/palace/state.md" in resp.sources


def test_wake_builder_truncates_oversized_first_compiled_card(tmp_path: Path) -> None:
    class FirstCardTokenCounter:
        def count(self, text: str, *, agent: str = "default") -> int:
            del agent
            if "Oversized compiled card line" in text:
                return max(5, text.count("Oversized compiled card line") * 20)
            return 1

    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "projects" / "palace").mkdir(parents=True)
    (tmp_path / "wiki" / "projects").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "projects" / "palace" / "state.md").write_text(
        "---\ntitle: Palace\nstatus: active\ncanonical: true\nsource_kind: canonical\ntemperature: hot\n---\n\nRaw state.\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "projects" / "palace.md").write_text(
        "---\ntitle: Palace compiled\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: hot\nsource_kind: generated\n---\n\n# Palace\n\n"
        + "\n".join(f"Oversized compiled card line {index}." for index in range(20))
        + "\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path, token_counter=FirstCardTokenCounter()).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=25, include_recent_sessions=0, project="palace")
    )

    assert "wiki/projects/palace.md" in resp.sources
    assert "wake section truncated" in resp.block
    assert "Oversized compiled card line 19." not in resp.block
    assert resp.tokens_estimated <= 25


def test_wake_builder_coding_profile_skips_general_compiled_concept_cards(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "wiki" / "people").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")

    # Compiled person card (active + canonical + hot)
    (tmp_path / "wiki" / "people" / "alice.md").write_text(
        "---\ntitle: Alice\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: hot\nsource_kind: generated\n---\n\n# Alice\n\nAlice is a key collaborator.\n",
        encoding="utf-8",
    )
    # Compiled concept card (active + canonical + warm)
    (tmp_path / "wiki" / "concepts" / "dory.md").write_text(
        "---\ntitle: Dory\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: warm\nsource_kind: generated\n---\n\n# Dory\n\nDory is the memory system.\n",
        encoding="utf-8",
    )
    # Cold card should NOT be included
    (tmp_path / "wiki" / "people" / "bob.md").write_text(
        "---\ntitle: Bob\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: cold\nsource_kind: generated\n---\n\n# Bob\n\nBob is a minor contact.\n",
        encoding="utf-8",
    )
    # Non-canonical card should NOT be included
    (tmp_path / "wiki" / "concepts" / "old.md").write_text(
        "---\ntitle: Old concept\ntype: wiki\nstatus: active\ncanonical: false\n"
        "temperature: warm\nsource_kind: human\n---\n\n# Old\n\nOld concept.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=800, include_recent_sessions=0)
    )

    # Coding wake should stay operational. Generic generated concept/person
    # cards belong in search/active-memory, not the startup block.
    assert "Dory is the memory system." not in resp.block
    assert "Alice is a key collaborator." not in resp.block
    assert "Bob is a minor contact." not in resp.block
    assert "Old concept." not in resp.block

    assert "wiki/people/alice.md" not in resp.sources
    assert "wiki/concepts/dory.md" not in resp.sources
    assert "wiki/people/bob.md" not in resp.sources
    assert "wiki/concepts/old.md" not in resp.sources


def test_wake_builder_general_compiled_cards_respect_profile(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "wiki" / "people").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "wiki" / "people" / "alice.md").write_text(
        "---\ntitle: Alice\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: hot\nupdated: 2026-05-01\nsource_kind: generated\n---\n\n# Alice\n\nAlice is a key collaborator.\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "concepts" / "dory.md").write_text(
        "---\ntitle: Dory\ntype: wiki\nstatus: active\ncanonical: true\n"
        "temperature: warm\nupdated: 2026-05-02\nsource_kind: generated\n---\n\n# Dory\n\nDory is the memory system.\n",
        encoding="utf-8",
    )

    privacy_resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="privacy", budget_tokens=800, include_recent_sessions=0)
    )
    default_resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="default", budget_tokens=800, include_recent_sessions=0)
    )

    assert "Alice is a key collaborator." not in privacy_resp.block
    assert "Dory is the memory system." not in privacy_resp.block
    assert "wiki/people/alice.md" not in privacy_resp.sources
    assert "wiki/concepts/dory.md" not in privacy_resp.sources
    assert "Alice is a key collaborator." in default_resp.block
    assert "Dory is the memory system." in default_resp.block

def test_wake_builder_noop_when_no_compiled_wiki_exists(tmp_path: Path) -> None:
    """Verify that wake behaves identically when no wiki/projects|people|concepts exist."""
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="coding", budget_tokens=400, include_recent_sessions=0)
    )

    assert "Current work." in resp.block
    assert resp.sources == ["core/active.md"]
    # No wiki cards
    assert "wiki/" not in " ".join(resp.sources)


def test_wake_builder_rejects_project_card_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "wiki" / "projects").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: wiki\nstatus: active\ncanonical: true\ntemperature: hot\n---\n\nLeaked active context.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="default", budget_tokens=800, include_recent_sessions=0, project="../../core/active")
    )

    assert resp.block.count("Leaked active context.") == 1
    assert "wiki/projects/../../core/active.md" not in resp.sources
    assert resp.sources == ["core/active.md"]

def test_wake_builder_skips_compiled_card_symlink_outside_root(tmp_path: Path) -> None:
    outside_root = tmp_path.parent / "outside-compiled-card.md"
    outside_root.write_text(
        "---\ntitle: Outside\ntype: wiki\nstatus: active\ncanonical: true\ntemperature: hot\n---\n\nLeaked outside content.\n",
        encoding="utf-8",
    )
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    (tmp_path / "wiki" / "concepts" / "outside.md").symlink_to(outside_root)

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="default", budget_tokens=800, include_recent_sessions=0)
    )

    assert "Leaked outside content." not in resp.block
    assert "wiki/concepts/outside.md" not in resp.sources

def test_wake_builder_limits_general_compiled_cards(tmp_path: Path) -> None:
    """Only up to max_general_cards (default 3) general compiled cards are included."""
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "wiki" / "people").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")

    for name in ("avery", "bob", "charlie", "diana", "eve"):
        (tmp_path / "wiki" / "people" / f"{name}.md").write_text(
            "---\ntitle: " + name.capitalize() + "\ntype: wiki\nstatus: active\ncanonical: true\n"
            "temperature: hot\nsource_kind: generated\n---\n\n# " + name.capitalize() + "\n\n" + name.capitalize() + " info.\n",
            encoding="utf-8",
        )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", profile="default", budget_tokens=2000, include_recent_sessions=0)
    )

    # Should have at most 3 general compiled cards (default max_general_cards)
    card_sources = [s for s in resp.sources if s.startswith("wiki/people/")]
    assert len(card_sources) <= 3, f"Expected at most 3 compiled cards, got {len(card_sources)}: {card_sources}"
    assert resp.block.count("info.") <= 3
