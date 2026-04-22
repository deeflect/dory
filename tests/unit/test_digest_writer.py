from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dory_core.digest_writer import (
    DailyDigest,
    DailyDigestWriter,
    DigestSessionSource,
    collect_daily_sessions,
    previous_day,
)


@dataclass(frozen=True, slots=True)
class _FakeDailyDigestGenerator:
    def generate(self, *, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> DailyDigest:
        return DailyDigest(
            title=f"Daily Digest - {target_date}",
            summary=f"Summarized {len(sessions)} sessions.",
            key_outcomes=("Dory session ingest was verified.",),
            decisions=("Keep digest generation reviewable.",),
            followups=("Run harness benchmark pass.",),
            projects=("dory",),
        )


@dataclass(slots=True)
class _RecordingDailyDigestGenerator:
    calls: list[tuple[DigestSessionSource, ...]]

    def generate(self, *, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> DailyDigest:
        self.calls.append(sessions)
        return DailyDigest(
            title=f"Daily Digest - {target_date}",
            summary="; ".join(session.path for session in sessions),
            key_outcomes=tuple(f"outcome:{session.session_id}" for session in sessions),
        )


def _write_session(corpus: Path, relative: str, *, updated: str, body: str = "User and assistant worked.") -> Path:
    path = corpus / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
title: test session
type: session
status: done
agent: codex
device: test-device
session_id: {path.stem}
updated: '{updated}'
---

{body}
""",
        encoding="utf-8",
    )
    return path


def test_collect_daily_sessions_uses_session_updated_date(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-a.md",
        updated="2026-04-20T10:00:00Z",
    )
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-19-session-b.md",
        updated="2026-04-19T10:00:00Z",
    )

    sessions = collect_daily_sessions(tmp_path, target_date="2026-04-20")

    assert len(sessions) == 1
    assert sessions[0].path == "logs/sessions/codex/test-device/2026-04-20-session-a.md"


def test_collect_daily_sessions_keeps_full_session_content_by_default(tmp_path: Path) -> None:
    body = "x" * 8000
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-a.md",
        updated="2026-04-20T10:00:00Z",
        body=body,
    )

    sessions = collect_daily_sessions(tmp_path, target_date="2026-04-20")

    assert sessions[0].content == body


def test_daily_digest_writer_summarizes_each_session_then_merges(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-a.md",
        updated="2026-04-20T10:00:00Z",
        body="First full session.",
    )
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-b.md",
        updated="2026-04-20T11:00:00Z",
        body="Second full session.",
    )
    generator = _RecordingDailyDigestGenerator(calls=[])

    result = DailyDigestWriter(tmp_path, generator).write(target_date="2026-04-20")

    assert result.written is True
    assert len(generator.calls) == 3
    assert [len(call) for call in generator.calls] == [1, 1, 2]
    assert generator.calls[0][0].content == "First full session."
    assert generator.calls[1][0].content == "Second full session."
    assert "Session-level digest for logs/sessions/codex/test-device/2026-04-20-session-a.md" in generator.calls[2][0].content


def test_daily_digest_writer_writes_generated_digest(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-a.md",
        updated="2026-04-20T10:00:00Z",
    )

    result = DailyDigestWriter(tmp_path, _FakeDailyDigestGenerator()).write(
        target_date="2026-04-20",
        min_session_age_seconds=0,
    )

    digest_path = tmp_path / "digests" / "daily" / "2026-04-20.md"
    assert result.written is True
    assert result.digest_path == "digests/daily/2026-04-20.md"
    assert digest_path.exists()
    written = digest_path.read_text(encoding="utf-8")
    assert "type: digest-daily" in written
    assert "Summarized 1 sessions." in written
    assert "logs/sessions/codex/test-device/2026-04-20-session-a.md" in written


def test_daily_digest_writer_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        "logs/sessions/codex/test-device/2026-04-20-session-a.md",
        updated="2026-04-20T10:00:00Z",
    )

    result = DailyDigestWriter(tmp_path, _FakeDailyDigestGenerator()).write(target_date="2026-04-20", dry_run=True)

    assert result.written is False
    assert result.dry_run is True
    assert result.content is not None
    assert not (tmp_path / "digests" / "daily" / "2026-04-20.md").exists()


def test_daily_digest_writer_refuses_overwrite_by_default(tmp_path: Path) -> None:
    digest_path = tmp_path / "digests" / "daily" / "2026-04-20.md"
    digest_path.parent.mkdir(parents=True)
    digest_path.write_text("existing\n", encoding="utf-8")

    result = DailyDigestWriter(tmp_path, _FakeDailyDigestGenerator()).write(target_date="2026-04-20")

    assert result.written is False
    assert result.skipped_reason == "digest already exists; pass overwrite=True to replace it"
    assert digest_path.read_text(encoding="utf-8") == "existing\n"


def test_previous_day_uses_reference_date() -> None:
    assert previous_day(reference=date(2026, 4, 20)) == "2026-04-19"
