from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dory_core.session_cleaner import CleanedSessionText, SessionCleaner
from dory_core.session_shipper import SessionShipJob


@dataclass(frozen=True, slots=True)
class SessionCapture:
    path: str
    agent: str
    device: str
    session_id: str
    status: str
    captured_from: str
    updated: str
    raw_text: str

    def clean(self, cleaner: SessionCleaner | None = None) -> CleanedSessionText:
        return (cleaner or SessionCleaner()).clean(self.raw_text)

    def to_ship_job(
        self,
        cleaner: SessionCleaner | None = None,
        cleaned: CleanedSessionText | None = None,
    ) -> SessionShipJob:
        cleaned = cleaned or self.clean(cleaner=cleaner)
        return SessionShipJob(
            path=self.path,
            content=cleaned.text,
            agent=self.agent,
            device=self.device,
            session_id=self.session_id,
            status=self.status,
            captured_from=self.captured_from,
            updated=self.updated,
        )

    @classmethod
    def from_file(
        cls,
        source: Path,
        *,
        path: str,
        agent: str,
        device: str,
        session_id: str,
        status: str,
        captured_from: str,
        updated: str,
    ) -> "SessionCapture":
        return cls(
            path=path,
            agent=agent,
            device=device,
            session_id=session_id,
            status=status,
            captured_from=captured_from,
            updated=updated,
            raw_text=Path(source).read_text(encoding="utf-8"),
        )
