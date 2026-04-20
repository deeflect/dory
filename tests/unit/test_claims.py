from __future__ import annotations

from dory_core.claims import Claim, EvidenceRef


def test_claim_to_dict_serializes_evidence() -> None:
    claim = Claim(
        id="rooster-focus",
        statement="Rooster is the active focus this week.",
        status="confirmed",
        confidence="high",
        freshness="fresh",
        sources=(
            EvidenceRef(
                path="core/active.md",
                line="1:4",
                surface="durable",
                note="Current state doc",
            ),
        ),
        last_reviewed="2026-04-13T10:00:00Z",
    )

    payload = claim.to_dict()

    assert payload["id"] == "rooster-focus"
    assert payload["sources"] == [
        {
            "path": "core/active.md",
            "line": "1:4",
            "surface": "durable",
            "note": "Current state doc",
        }
    ]
