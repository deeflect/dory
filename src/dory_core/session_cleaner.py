from __future__ import annotations

from dataclasses import dataclass
import re

_SECRET_ENV_PATTERN = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PWD|AUTH))=([^\s\"'`]+)"
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._-]+\b")
_GENERIC_SECRET_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9_-]+)\b")

_DROP_LINE_PATTERNS = (
    re.compile(r"^⏺\s+.*\((?:MCP|tool|tool call)\)\s*$"),
    re.compile(r"^(?:tool|action|call|invoke|mcp tool):\s*", re.IGNORECASE),
    re.compile(r"^\s*tool call\s*[:\-]", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class CleanedSessionText:
    text: str
    original_chars: int
    cleaned_chars: int
    dropped_lines: int
    redactions: int


@dataclass(frozen=True, slots=True)
class SessionCleaner:
    """Remove tool spam and obvious secrets from a captured session log."""

    def clean(self, raw_text: str) -> CleanedSessionText:
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned_lines: list[str] = []
        dropped_lines = 0
        redactions = 0

        for line in normalized.splitlines():
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue

            if self._should_drop_line(stripped):
                dropped_lines += 1
                continue

            redacted_line, line_redactions = self._redact_line(line)
            redactions += line_redactions
            cleaned_lines.append(redacted_line)

        cleaned_text = _collapse_blank_lines("\n".join(cleaned_lines)).strip()
        return CleanedSessionText(
            text=cleaned_text,
            original_chars=len(raw_text),
            cleaned_chars=len(cleaned_text),
            dropped_lines=dropped_lines,
            redactions=redactions,
        )

    @staticmethod
    def _should_drop_line(line: str) -> bool:
        return any(pattern.search(line) for pattern in _DROP_LINE_PATTERNS)

    @staticmethod
    def _redact_line(line: str) -> tuple[str, int]:
        redactions = 0
        redacted = line
        for pattern in (_SECRET_ENV_PATTERN, _BEARER_PATTERN, _GENERIC_SECRET_PATTERN):
            redacted, count = pattern.subn(_redaction_replacement, redacted)
            redactions += count
        return redacted, redactions


def clean_session_text(raw_text: str) -> CleanedSessionText:
    return SessionCleaner().clean(raw_text)


def _redaction_replacement(match: re.Match[str]) -> str:
    if match.re is _SECRET_ENV_PATTERN:
        return f"{match.group(1)}=[REDACTED]"
    return "[REDACTED]"


def _collapse_blank_lines(text: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for line in text.splitlines():
        if line.strip():
            lines.append(line.rstrip())
            previous_blank = False
            continue
        if previous_blank:
            continue
        lines.append("")
        previous_blank = True
    return "\n".join(lines)
