from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RerankMode = Literal["auto", "true", "false"]


@dataclass(frozen=True, slots=True)
class RerankDecision:
    requested: RerankMode
    phase: str
    enabled: bool
    reason: str


def resolve_rerank_mode(requested: RerankMode, *, phase: str = "v1") -> RerankDecision:
    if requested == "true":
        return RerankDecision(
            requested=requested,
            phase=phase,
            enabled=True,
            reason="rerank explicitly enabled",
        )

    if requested == "false":
        return RerankDecision(
            requested=requested,
            phase=phase,
            enabled=False,
            reason="rerank explicitly disabled",
        )

    if phase == "v0":
        return RerankDecision(
            requested=requested,
            phase=phase,
            enabled=False,
            reason="rerank disabled in v0",
        )

    return RerankDecision(
        requested=requested,
        phase=phase,
        enabled=True,
        reason=f"rerank enabled in {phase}",
    )
