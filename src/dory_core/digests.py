from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.types import DigestReq, DigestResp


_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
_WEEK_RE = re.compile(r"(?P<week>\d{4}-W\d{2})")


@dataclass(frozen=True, slots=True)
class DigestCandidate:
    path: Path
    kind: str
    period: str
    priority: int


class DigestReader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, req: DigestReq) -> DigestResp:
        candidate = self._resolve(req)
        if candidate is None:
            available = [item.path.as_posix() for item in self._candidates(req.kind)]
            selector = req.date if req.kind == "daily" else req.week
            return DigestResp(
                found=False,
                kind=req.kind,
                period=selector or "latest",
                path=None,
                content="",
                available=available[:20],
            )

        target = self.root / candidate.path
        text = target.read_text(encoding="utf-8")
        sliced = _slice_lines(text, req.from_line, req.lines)
        frontmatter: dict[str, object] = {}
        try:
            frontmatter = load_markdown_document(text).frontmatter
        except ValueError:
            frontmatter = {}
        return DigestResp(
            found=True,
            kind=candidate.kind,
            period=candidate.period,
            path=candidate.path.as_posix(),
            content=sliced,
            from_line=req.from_line,
            lines_returned=len(sliced.splitlines()) if sliced else 0,
            total_lines=len(text.splitlines()),
            frontmatter=frontmatter,
            hash=f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
            available=[],
        )

    def _resolve(self, req: DigestReq) -> DigestCandidate | None:
        selector = self._selector(req)
        candidates = self._candidates(req.kind)
        if selector is not None:
            matches = [candidate for candidate in candidates if candidate.period == selector]
            if matches:
                return _newest(matches)
            return None
        return _newest(candidates)

    def _selector(self, req: DigestReq) -> str | None:
        if req.kind == "daily":
            return _normalize_daily_selector(req.date)
        return _normalize_weekly_selector(req.week)

    def _candidates(self, kind: str) -> list[DigestCandidate]:
        if kind == "daily":
            return [
                candidate
                for root, priority in (("digests/daily", 2), ("logs/daily", 1))
                for candidate in self._scan(root=root, kind="daily", pattern=_DATE_RE, priority=priority)
            ]
        return [
            candidate
            for root, priority in (("digests/weekly", 2), ("logs/weekly", 1))
            for candidate in self._scan(root=root, kind="weekly", pattern=_WEEK_RE, priority=priority)
        ]

    def _scan(self, *, root: str, kind: str, pattern: re.Pattern[str], priority: int) -> list[DigestCandidate]:
        digest_root = self.root / root
        if not digest_root.exists():
            return []
        candidates: list[DigestCandidate] = []
        for path in sorted(digest_root.glob("*.md")):
            rel = path.relative_to(self.root)
            match = pattern.search(path.stem)
            if match is None:
                continue
            period = match.group("date") if kind == "daily" else match.group("week")
            candidates.append(DigestCandidate(path=rel, kind=kind, period=period, priority=priority))
        return candidates


def _normalize_daily_selector(value: str | None) -> str | None:
    if value is None or value == "latest":
        return None
    if value == "today":
        return date.today().isoformat()
    if value == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if not _DATE_RE.fullmatch(value):
        raise ValueError("daily digest date must be YYYY-MM-DD, today, yesterday, or latest")
    return value


def _normalize_weekly_selector(value: str | None) -> str | None:
    if value is None or value == "latest":
        return None
    if value == "current":
        return _iso_week(date.today())
    if value == "previous":
        return _iso_week(date.today() - timedelta(days=7))
    if not _WEEK_RE.fullmatch(value):
        raise ValueError("weekly digest week must be YYYY-Www, current, previous, or latest")
    return value


def _iso_week(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _newest(candidates: list[DigestCandidate]) -> DigestCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.period, candidate.priority, candidate.path.as_posix()))


def _slice_lines(text: str, start_line: int, limit: int | None) -> str:
    lines = text.splitlines()
    start_index = max(start_line, 1) - 1
    if limit is None:
        return "\n".join(lines[start_index:])
    return "\n".join(lines[start_index : start_index + limit])
