from __future__ import annotations

import json
from pathlib import Path

from dory_core.config import DorySettings
from dory_core.dreaming.extract import resolve_dream_backend
from dory_core.dreaming.proposals import ProposalGenerator


def test_proposal_generator_writes_reviewable_json(tmp_path: Path) -> None:
    distilled_path = tmp_path / "inbox" / "distilled" / "codex-2026-04-07.md"
    distilled_path.parent.mkdir(parents=True, exist_ok=True)
    distilled_path.write_text("A distilled summary for review.\n", encoding="utf-8")

    generator = ProposalGenerator(
        root=tmp_path,
        backend=resolve_dream_backend(DorySettings()),
    )

    target = generator.generate(distilled_path)

    assert target == tmp_path / "inbox" / "proposed" / "codex-2026-04-07.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["backend"] == "openrouter"
    assert payload["source_distilled_path"] == str(distilled_path)
    assert payload["actions"] == []


def test_proposal_generator_uses_openrouter_actions_when_available(tmp_path: Path) -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {
                "actions": [
                    {
                        "action": "write",
                        "kind": "decision",
                        "subject": "clawsy",
                        "content": "Pricing moved to BYOK tiers.",
                        "scope": "project",
                        "confidence": "high",
                        "reason": "Grounded in distilled session facts.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ]
            }

    distilled_path = tmp_path / "inbox" / "distilled" / "codex-2026-04-10.md"
    distilled_path.parent.mkdir(parents=True, exist_ok=True)
    distilled_path.write_text("Distilled summary.\n", encoding="utf-8")

    generator = ProposalGenerator(
        root=tmp_path,
        backend=resolve_dream_backend(DorySettings()),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    target = generator.generate(distilled_path)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["actions"][0]["action"] == "write"
    assert payload["actions"][0]["kind"] == "decision"
    assert payload["actions"][0]["subject"] == "clawsy"
