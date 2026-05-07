from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from dory_core.digests import DigestReader
from dory_core.types import DigestReq


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_digest_reader_returns_latest_daily_digest_from_digest_tree(tmp_path: Path) -> None:
    older = "---\ntitle: older\ntype: digest-daily\n---\n\nOlder recap.\n"
    latest = "---\ntitle: latest\ntype: digest-daily\n---\n\nLatest recap.\n"
    _write(tmp_path / "digests" / "daily" / "2026-05-01.md", older)
    _write(tmp_path / "digests" / "daily" / "2026-05-07.md", latest)

    result = DigestReader(tmp_path).read(DigestReq())

    assert result.found is True
    assert result.kind == "daily"
    assert result.period == "2026-05-07"
    assert result.path == "digests/daily/2026-05-07.md"
    assert "Latest recap." in result.content
    assert result.hash == f"sha256:{sha256(latest.encode('utf-8')).hexdigest()}"


def test_digest_reader_falls_back_to_legacy_daily_logs(tmp_path: Path) -> None:
    _write(
        tmp_path / "logs" / "daily" / "2026-05-06-digest.md",
        "---\ntitle: log digest\ntype: daily\n---\n\nLegacy log recap.\n",
    )

    result = DigestReader(tmp_path).read(DigestReq(date="2026-05-06"))

    assert result.found is True
    assert result.path == "logs/daily/2026-05-06-digest.md"
    assert result.period == "2026-05-06"


def test_digest_reader_returns_latest_weekly_digest(tmp_path: Path) -> None:
    _write(
        tmp_path / "digests" / "weekly" / "2026-W18.md",
        "---\ntitle: weekly\ntype: digest-weekly\n---\n\nWeekly recap.\n",
    )

    result = DigestReader(tmp_path).read(DigestReq(kind="weekly"))

    assert result.found is True
    assert result.kind == "weekly"
    assert result.period == "2026-W18"
    assert result.path == "digests/weekly/2026-W18.md"


def test_digest_reader_reports_available_paths_when_missing(tmp_path: Path) -> None:
    _write(tmp_path / "digests" / "daily" / "2026-05-07.md", "---\ntitle: latest\n---\n\nLatest.\n")

    result = DigestReader(tmp_path).read(DigestReq(date="2026-05-01"))

    assert result.found is False
    assert result.period == "2026-05-01"
    assert result.available == ["digests/daily/2026-05-07.md"]


def test_digest_reader_rejects_invalid_daily_selector(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="daily digest date"):
        DigestReader(tmp_path).read(DigestReq(date="last week"))
