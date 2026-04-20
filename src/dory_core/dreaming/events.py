from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionClosedEvent:
    agent: str
    session_path: str
    closed_at: datetime

    @property
    def output_path(self) -> str:
        session_name = Path(self.session_path).stem
        return f"inbox/distilled/{self.agent}-{session_name}.md"

    @classmethod
    def now(cls, agent: str, session_path: str) -> "SessionClosedEvent":
        return cls(agent=agent, session_path=session_path, closed_at=datetime.now(tz=UTC))
