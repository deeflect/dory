from __future__ import annotations

from pathlib import Path

from dory_core.hot_context import render_packet_to_block
from dory_core.types import WakeReq
from dory_core.wake import WakeBuilder


def test_wake_builder_returns_frozen_block(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=120))

    assert resp.tokens_estimated > 0
    assert resp.block.startswith("---")
    assert resp.frozen_at.tzinfo is not None


def test_wake_builder_can_emit_hot_context_packet(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")
    builder = WakeBuilder(tmp_path)
    req = WakeReq(agent="claude-code", budget_tokens=300, include_recent_sessions=0)

    resp = builder.build(req)
    packet = builder.build_packet(req)

    assert packet.profile == resp.profile
    assert list(packet.sources) == resp.sources
    assert packet.wake_context
    assert packet.wake_context[0].text == resp.block
    assert render_packet_to_block(packet, budget_tokens=300) == resp.block


def test_wake_builder_packet_preserves_warnings(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace" / "unknown"
    workspace.mkdir(parents=True)
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nGlobal context.\n", encoding="utf-8")
    builder = WakeBuilder(tmp_path)
    req = WakeReq(agent="codex", profile="coding", budget_tokens=300, include_recent_sessions=0, cwd=str(workspace))

    packet = builder.build_packet(req)

    assert packet.partial
    assert packet.warnings == (
        "Project or cwd did not resolve to a known project; coding wake skipped global active context.",
    )


def test_wake_builder_loads_custom_profile_sections(tmp_path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "profiles" / "brand").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("Dory maintenance should not leak into brand wake.\n", encoding="utf-8")
    (tmp_path / "profiles" / "brand" / "default.md").write_text(
        "Brand voice is artifact-led.\nSecond line should be truncated.\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles" / "brand" / "active.md").write_text("Brand active work is launch copy.\n", encoding="utf-8")
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  brand:
    wake:
      sections:
        - profiles/brand/default.md
        - profiles/brand/active.md
      budgets:
        default: 0
""".strip(),
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(WakeReq(agent="claude-code", profile="brand", budget_tokens=200))

    assert resp.profile == "brand"
    assert resp.sources == ["profiles/brand/default.md", "profiles/brand/active.md"]
    assert "wake excerpt truncated" in resp.block
    assert "Brand active work is launch copy." in resp.block
    assert "Dory maintenance" not in resp.block


def test_wake_builder_rejects_custom_profile_sections_outside_corpus(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("Outside corpus context must not load.\n", encoding="utf-8")
    (tmp_path / "profiles.yaml").write_text(
        f"""
profiles:
  unsafe:
    wake:
      sections:
        - ../{outside.name}
""".strip(),
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(WakeReq(agent="claude-code", profile="unsafe", budget_tokens=200))

    assert resp.block == ""
    assert resp.sources == []


def test_writing_profile_applies_writing_voice_budget(tmp_path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "knowledge" / "personal").mkdir(parents=True)
    (tmp_path / "core" / "soul.md").write_text("Soul note\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("Defaults note\n", encoding="utf-8")
    (tmp_path / "core" / "active.md").write_text("Active note\n", encoding="utf-8")
    (tmp_path / "knowledge" / "personal" / "writing-voice.md").write_text(
        "Voice line one.\nVoice line two should be truncated.\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  writing:
    wake:
      budgets:
        writing_voice: 0
""".strip(),
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(WakeReq(agent="claude-code", profile="writing", budget_tokens=300))

    assert "wake excerpt truncated" in resp.block
    assert "Voice line two should be truncated." not in resp.block


def test_wake_builder_unknown_profile_raises(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("# Active\n\nCurrent work.\n", encoding="utf-8")

    from dory_core.errors import DoryValidationError
    import pytest

    with pytest.raises(DoryValidationError, match="Unknown retrieval profile"):
        WakeBuilder(tmp_path).build(
            WakeReq(agent="codex", profile="nonexistent", budget_tokens=200, include_recent_sessions=0)
        )


def test_wake_builder_injects_project_state_from_explicit_param(tmp_path: Path) -> None:
    """WakeBuilder includes project state when project name is explicitly provided."""
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("Dory is active.\n", encoding="utf-8")
    (tmp_path / "projects" / "dory").mkdir(parents=True)
    (tmp_path / "projects" / "dory" / "state.md").write_text(
        "---\ntitle: Dory\ntype: project\nstatus: active\ncanonical: true\n---\n\n## Summary\n- Dory is the shared memory substrate for agents.\n",
        encoding="utf-8",
    )

    resp = WakeBuilder(tmp_path).build(
        WakeReq(agent="codex", project="dory", budget_tokens=1200, include_recent_sessions=0, include_pinned_decisions=False)
    )

    assert resp.block
    assert "Dory is the shared memory substrate for agents." in resp.block
    assert "projects/dory/state.md" in resp.sources
    assert resp.profile == "default"
