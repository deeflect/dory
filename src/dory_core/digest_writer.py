from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.fs import atomic_write_text
from dory_core.llm.json_client import JSONGenerationClient


@dataclass(frozen=True, slots=True)
class DigestSessionSource:
    path: str
    agent: str
    session_id: str
    updated: str
    content: str


@dataclass(frozen=True, slots=True)
class DailyDigest:
    title: str
    summary: str
    key_outcomes: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DailyDigestResult:
    digest_path: str
    date: str
    sessions_considered: int
    sessions_included: tuple[str, ...]
    written: bool
    dry_run: bool = False
    skipped_reason: str | None = None
    reindexed: bool = False
    content: str | None = None


class DailyDigestGenerator(Protocol):
    def generate(self, *, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> DailyDigest: ...


_DAILY_DIGEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_outcomes": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "followups": {"type": "array", "items": {"type": "string"}},
        "projects": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "key_outcomes", "decisions", "followups", "projects"],
}

_SYSTEM_PROMPT = (
    "You write Dory daily digests from AI-agent session logs.\n"
    "Use only the provided session text. Do not infer missing facts or fill gaps from prior knowledge.\n"
    "Preserve durable memory signal: project progress, decisions, current state, bugs fixed, regressions, "
    "operations/config changes, tests run, deployments, blockers, and explicit follow-ups.\n"
    "Ignore transient chatter, repeated tool output, raw stack traces, low-value status updates, and abandoned branches "
    "unless they explain a durable outcome.\n"
    "Never include secrets, bearer tokens, passwords, private keys, cookie values, API keys, or raw credentials. "
    "If a session involved credentials, summarize only the safe operational fact, such as that auth was configured or rotated.\n"
    "Avoid private personal details unless they are explicitly framed as a durable preference, boundary, or safety rule.\n"
    "When multiple sessions are provided, merge duplicates and preserve source-grounded specificity.\n"
    "If there is no durable signal, say so plainly instead of inventing outcomes.\n"
    "Write compactly for later memory mining."
)


@dataclass(frozen=True, slots=True)
class LLMDailyDigestGenerator:
    client: JSONGenerationClient

    def generate(self, *, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> DailyDigest:
        payload = self.client.generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_digest_prompt(target_date=target_date, sessions=sessions),
            schema_name="dory_daily_digest",
            schema=_DAILY_DIGEST_SCHEMA,
        )
        return _coerce_daily_digest(payload, target_date=target_date)


OpenRouterDailyDigestGenerator = LLMDailyDigestGenerator


@dataclass(frozen=True, slots=True)
class DailyDigestWriter:
    corpus_root: Path
    generator: DailyDigestGenerator
    max_session_chars: int | None = None
    max_total_chars: int | None = None
    batch_max_chars: int = 180_000
    skip_tiny_chars: int = 0

    def write(
        self,
        *,
        target_date: str,
        overwrite: bool = False,
        dry_run: bool = False,
        min_session_age_seconds: float = 0,
        limit: int | None = None,
    ) -> DailyDigestResult:
        digest_rel = Path("digests") / "daily" / f"{target_date}.md"
        digest_path = self.corpus_root / digest_rel
        if digest_path.exists() and not overwrite:
            return DailyDigestResult(
                digest_path=digest_rel.as_posix(),
                date=target_date,
                sessions_considered=0,
                sessions_included=(),
                written=False,
                dry_run=dry_run,
                skipped_reason="digest already exists; pass overwrite=True to replace it",
            )

        sessions = collect_daily_sessions(
            self.corpus_root,
            target_date=target_date,
            min_session_age_seconds=min_session_age_seconds,
            max_session_chars=self.max_session_chars,
            max_total_chars=self.max_total_chars,
            limit=limit,
        )
        if not sessions:
            return DailyDigestResult(
                digest_path=digest_rel.as_posix(),
                date=target_date,
                sessions_considered=0,
                sessions_included=(),
                written=False,
                dry_run=dry_run,
                skipped_reason="no session logs found for date",
            )

        digest = self._generate_digest(target_date=target_date, sessions=sessions)
        content = render_daily_digest(target_date=target_date, digest=digest, sessions=sessions)
        if dry_run:
            return DailyDigestResult(
                digest_path=digest_rel.as_posix(),
                date=target_date,
                sessions_considered=len(sessions),
                sessions_included=tuple(session.path for session in sessions),
                written=False,
                dry_run=True,
                content=content,
            )

        atomic_write_text(digest_path, content, encoding="utf-8")
        return DailyDigestResult(
            digest_path=digest_rel.as_posix(),
            date=target_date,
            sessions_considered=len(sessions),
            sessions_included=tuple(session.path for session in sessions),
            written=True,
        )

    def _generate_digest(self, *, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> DailyDigest:
        if len(sessions) <= 1:
            return self.generator.generate(target_date=target_date, sessions=sessions)
        session_digests: list[tuple[tuple[DigestSessionSource, ...], DailyDigest]] = []
        for batch in batch_daily_sessions(sessions, max_chars=self.batch_max_chars, skip_tiny_chars=self.skip_tiny_chars):
            session_digests.append((batch, self.generator.generate(target_date=target_date, sessions=batch)))
        return self.generator.generate(
            target_date=target_date,
            sessions=tuple(
                _batch_digest_source(batch=batch, digest=digest)
                for batch, digest in session_digests
            ),
        )


def collect_daily_sessions(
    corpus_root: Path,
    *,
    target_date: str,
    min_session_age_seconds: float = 0,
    max_session_chars: int | None = None,
    max_total_chars: int | None = None,
    limit: int | None = None,
) -> tuple[DigestSessionSource, ...]:
    sessions_root = corpus_root / "logs" / "sessions"
    if not sessions_root.exists():
        return ()

    now = datetime.now(tz=UTC)
    sessions: list[DigestSessionSource] = []
    total_chars = 0
    for path in sorted(sessions_root.rglob("*.md")):
        if min_session_age_seconds > 0:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if (now - modified_at).total_seconds() < min_session_age_seconds:
                continue
        source = _load_session_source(
            corpus_root=corpus_root,
            path=path,
            max_chars=max_session_chars,
        )
        if source is None or _date_from_session(source) != target_date:
            continue
        if max_total_chars is not None and total_chars + len(source.content) > max_total_chars and sessions:
            break
        sessions.append(source)
        total_chars += len(source.content)
        if limit is not None and len(sessions) >= limit:
            break
    return tuple(sessions)


def batch_daily_sessions(
    sessions: tuple[DigestSessionSource, ...],
    *,
    max_chars: int = 180_000,
    skip_tiny_chars: int = 0,
) -> tuple[tuple[DigestSessionSource, ...], ...]:
    eligible = tuple(session for session in sessions if len(session.content.strip()) >= skip_tiny_chars)
    if not eligible:
        return ()
    if max_chars <= 0:
        return tuple((session,) for session in eligible)

    batches: list[tuple[DigestSessionSource, ...]] = []
    current: list[DigestSessionSource] = []
    current_chars = 0
    for session in eligible:
        session_chars = _digest_prompt_session_chars(session)
        if session_chars > max_chars:
            if current:
                batches.append(tuple(current))
                current = []
                current_chars = 0
            batches.append((session,))
            continue
        if current and current_chars + session_chars > max_chars:
            batches.append(tuple(current))
            current = []
            current_chars = 0
        current.append(session)
        current_chars += session_chars
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def render_daily_digest(
    *,
    target_date: str,
    digest: DailyDigest,
    sessions: tuple[DigestSessionSource, ...],
) -> str:
    frontmatter: dict[str, Any] = {
        "title": digest.title or f"Daily Digest - {target_date}",
        "type": "digest-daily",
        "status": "active",
        "source_kind": "generated",
        "temperature": "warm",
        "canonical": False,
        "date": target_date,
        "created": target_date,
        "updated": datetime.now(tz=UTC).date().isoformat(),
        "source_sessions": [session.path for session in sessions],
    }
    sections = [
        f"# {digest.title or f'Daily Digest - {target_date}'}",
        "",
        "## Summary",
        digest.summary.strip() or "No summary generated.",
    ]
    sections.extend(_render_bullets("## Key Outcomes", digest.key_outcomes))
    sections.extend(_render_bullets("## Decisions", digest.decisions))
    sections.extend(_render_bullets("## Follow-ups", digest.followups))
    sections.extend(_render_bullets("## Projects", digest.projects))
    sections.extend(
        [
            "",
            "## Source Sessions",
            *[f"- `{session.path}`" for session in sessions],
        ]
    )
    return dump_markdown_document(frontmatter, "\n".join(sections))


def previous_day(reference: date | None = None) -> str:
    current = reference or date.today()
    return date.fromordinal(current.toordinal() - 1).isoformat()


def _load_session_source(*, corpus_root: Path, path: Path, max_chars: int | None) -> DigestSessionSource | None:
    text = path.read_text(encoding="utf-8")
    try:
        document = load_markdown_document(text)
        frontmatter = document.frontmatter
        body = document.body
    except ValueError:
        frontmatter = {}
        body = text
    relative_path = path.relative_to(corpus_root).as_posix()
    updated = _coerce_string(frontmatter.get("updated")) or _date_from_path(path)
    content = body.strip()
    if not content:
        return None
    return DigestSessionSource(
        path=relative_path,
        agent=_coerce_string(frontmatter.get("agent")) or _infer_agent_from_path(relative_path),
        session_id=_coerce_string(frontmatter.get("session_id")) or path.stem,
        updated=updated,
        content=_truncate_session_content(content, max_chars=max_chars),
    )


def _build_digest_prompt(*, target_date: str, sessions: tuple[DigestSessionSource, ...]) -> str:
    rendered_sessions: list[str] = []
    for session in sessions:
        rendered_sessions.append(
            "\n".join(
                [
                    f"### {session.path}",
                    f"agent: {session.agent}",
                    f"session_id: {session.session_id}",
                    f"updated: {session.updated}",
                    "",
                    session.content,
                ]
            )
        )
    return f"Digest date: {target_date}\n\nSession logs:\n\n" + "\n\n---\n\n".join(rendered_sessions)


def _batch_digest_source(*, batch: tuple[DigestSessionSource, ...], digest: DailyDigest) -> DigestSessionSource:
    primary = batch[0]
    path = primary.path if len(batch) == 1 else f"batch:{primary.updated[:10]}:{primary.session_id}:{len(batch)}-sessions"
    sections = [
        "Batch-level digest for:",
        *[f"- {session.path}" for session in batch],
        "",
        "Summary:",
        digest.summary.strip() or "No summary generated.",
    ]
    sections.extend(_render_bullets("Key Outcomes", digest.key_outcomes))
    sections.extend(_render_bullets("Decisions", digest.decisions))
    sections.extend(_render_bullets("Follow-ups", digest.followups))
    sections.extend(_render_bullets("Projects", digest.projects))
    return DigestSessionSource(
        path=path,
        agent=primary.agent if len({session.agent for session in batch}) == 1 else "mixed",
        session_id=primary.session_id if len(batch) == 1 else f"{primary.session_id}-batch-{len(batch)}",
        updated=primary.updated,
        content="\n".join(sections),
    )


def _digest_prompt_session_chars(session: DigestSessionSource) -> int:
    return len(session.content) + len(session.path) + len(session.agent) + len(session.session_id) + len(session.updated) + 64


def _coerce_daily_digest(payload: object, *, target_date: str) -> DailyDigest:
    if not isinstance(payload, dict):
        return DailyDigest(title=f"Daily Digest - {target_date}", summary="No summary generated.")
    return DailyDigest(
        title=_coerce_string(payload.get("title")) or f"Daily Digest - {target_date}",
        summary=_coerce_string(payload.get("summary")) or "No summary generated.",
        key_outcomes=_coerce_string_tuple(payload.get("key_outcomes")),
        decisions=_coerce_string_tuple(payload.get("decisions")),
        followups=_coerce_string_tuple(payload.get("followups")),
        projects=_coerce_string_tuple(payload.get("projects")),
    )


def _render_bullets(title: str, items: tuple[str, ...]) -> list[str]:
    rendered = ["", title]
    if not items:
        rendered.append("- None")
        return rendered
    rendered.extend(f"- {item}" for item in items)
    return rendered


def _date_from_session(source: DigestSessionSource) -> str | None:
    if len(source.updated) >= 10:
        return source.updated[:10]
    return None


def _date_from_path(path: Path) -> str:
    stem = path.stem
    if len(stem) >= 10 and stem[:4].isdigit():
        return stem[:10]
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return modified_at.date().isoformat()


def _infer_agent_from_path(path: str) -> str:
    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == "logs" and parts[1] == "sessions":
        return parts[2]
    return "unknown"


def _truncate_session_content(content: str, *, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0 or len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n\n[session truncated for daily digest input]"


def _coerce_string(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _coerce_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.split())
        if not cleaned or cleaned.casefold() in seen:
            continue
        seen.add(cleaned.casefold())
        items.append(cleaned)
    return tuple(items)
