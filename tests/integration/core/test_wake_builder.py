from __future__ import annotations

from dory_core.types import WakeReq
from dory_core.wake import WakeBuilder


def test_wake_builder_returns_frozen_block(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=120))

    assert resp.tokens_estimated > 0
    assert resp.block.startswith("---")
    assert resp.frozen_at.tzinfo is not None


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
