from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
import json
import os
import sqlite3

from dory_core.session_capture import SessionCapture


SessionSpeaker = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class SessionTurn:
    speaker: SessionSpeaker
    text: str


@dataclass(frozen=True, slots=True)
class CollectedSession:
    capture: SessionCapture
    source_key: str
    source_version: str


@dataclass(slots=True)
class CollectorState:
    versions: dict[str, str] = field(default_factory=dict)

    def version_for(self, source_key: str) -> str | None:
        return self.versions.get(source_key)

    def update(self, source_key: str, source_version: str) -> None:
        self.versions[source_key] = source_version


@dataclass(frozen=True, slots=True)
class CollectorStateStore:
    path: Path

    def load(self) -> CollectorState:
        if not self.path.exists():
            return CollectorState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise ValueError(f"invalid collector checkpoint JSON: {self.path}") from err
        if not isinstance(payload, dict):
            raise ValueError(f"collector checkpoint must be an object: {self.path}")
        versions = payload.get("versions", {})
        if not isinstance(versions, dict):
            raise ValueError(f"collector checkpoint versions must be an object: {self.path}")
        return CollectorState(versions={str(key): str(value) for key, value in versions.items()})

    def save(self, state: CollectorState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"versions": state.versions}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class SessionCollector(Protocol):
    name: str

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]: ...


@dataclass(frozen=True, slots=True)
class ClaudeProjectsCollector:
    root: Path
    include_subagents: bool = False
    name: str = "claude"

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]:
        captures: list[CollectedSession] = []
        if not self.root.exists():
            return ()
        for path in sorted(self.root.rglob("*.jsonl")):
            if not self.include_subagents and "subagents" in path.parts:
                continue
            source_key = f"claude:{path}"
            source_version = _stat_version(path)
            if state.version_for(source_key) == source_version:
                continue
            rendered = _parse_claude_jsonl(path)
            if rendered is None:
                continue
            session_id = rendered.session_id or path.stem
            target_path = _target_path(agent="claude", device=device, updated=rendered.updated, session_id=session_id)
            capture = SessionCapture(
                path=target_path,
                agent="claude",
                device=device,
                session_id=session_id,
                status=_infer_status(rendered.updated),
                captured_from="claude-projects-jsonl",
                updated=rendered.updated,
                raw_text=_render_session_log(
                    harness="claude",
                    session_id=session_id,
                    updated=rendered.updated,
                    metadata={
                        "cwd": rendered.cwd,
                        "git_branch": rendered.git_branch,
                        "source_path": str(path),
                    },
                    turns=rendered.turns,
                ),
            )
            captures.append(CollectedSession(capture=capture, source_key=source_key, source_version=source_version))
        return tuple(captures)


@dataclass(frozen=True, slots=True)
class CodexSessionsCollector:
    root: Path
    name: str = "codex"

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]:
        captures: list[CollectedSession] = []
        if not self.root.exists():
            return ()
        for path in sorted(self.root.rglob("*.jsonl")):
            source_key = f"codex:{path}"
            source_version = _stat_version(path)
            if state.version_for(source_key) == source_version:
                continue
            rendered = _parse_codex_jsonl(path)
            if rendered is None:
                continue
            session_id = rendered.session_id or path.stem
            target_path = _target_path(agent="codex", device=device, updated=rendered.updated, session_id=session_id)
            capture = SessionCapture(
                path=target_path,
                agent="codex",
                device=device,
                session_id=session_id,
                status=_infer_status(rendered.updated),
                captured_from="codex-sessions-jsonl",
                updated=rendered.updated,
                raw_text=_render_session_log(
                    harness="codex",
                    session_id=session_id,
                    updated=rendered.updated,
                    metadata={
                        "cwd": rendered.cwd,
                        "agent_nickname": rendered.agent_nickname,
                        "agent_role": rendered.agent_role,
                        "source_path": str(path),
                    },
                    turns=rendered.turns,
                ),
            )
            captures.append(CollectedSession(capture=capture, source_key=source_key, source_version=source_version))
        return tuple(captures)


@dataclass(frozen=True, slots=True)
class OpenCodeCollector:
    db_path: Path
    name: str = "opencode"

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]:
        if not self.db_path.exists():
            return ()
        with sqlite3.connect(self.db_path) as connection:
            session_rows = connection.execute(
                """
                SELECT id, directory, title, time_updated
                FROM session
                WHERE time_archived IS NULL
                ORDER BY time_updated ASC
                """
            ).fetchall()
            captures: list[CollectedSession] = []
            for session_id, directory, title, time_updated in session_rows:
                source_key = f"opencode:{session_id}"
                source_version = str(int(time_updated))
                if state.version_for(source_key) == source_version:
                    continue
                turns = _load_opencode_turns(connection, session_id)
                if not turns:
                    continue
                updated = _iso_from_millis(int(time_updated))
                target_path = _target_path(agent="opencode", device=device, updated=updated, session_id=str(session_id))
                capture = SessionCapture(
                    path=target_path,
                    agent="opencode",
                    device=device,
                    session_id=str(session_id),
                    status=_infer_status(updated),
                    captured_from="opencode-sqlite",
                    updated=updated,
                    raw_text=_render_session_log(
                        harness="opencode",
                        session_id=str(session_id),
                        updated=updated,
                        metadata={
                            "title": str(title or ""),
                            "cwd": str(directory or ""),
                            "source_path": str(self.db_path),
                        },
                        turns=turns,
                    ),
                )
                captures.append(CollectedSession(capture=capture, source_key=source_key, source_version=source_version))
        return tuple(captures)


@dataclass(frozen=True, slots=True)
class OpenClawSessionsCollector:
    root: Path
    name: str = "openclaw"

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]:
        captures: list[CollectedSession] = []
        if not self.root.exists():
            return ()
        for path in _iter_openclaw_session_files(self.root):
            if path.name == "sessions.jsonl":
                continue
            source_key = f"openclaw:{path}"
            source_version = _stat_version(path)
            if state.version_for(source_key) == source_version:
                continue
            agent_id = path.parent.parent.name
            rendered = _parse_openclaw_jsonl(path)
            if rendered is None:
                continue
            session_id = rendered.session_id or path.stem
            target_path = _target_path(agent="openclaw", device=device, updated=rendered.updated, session_id=session_id)
            capture = SessionCapture(
                path=target_path,
                agent="openclaw",
                device=device,
                session_id=session_id,
                status=_infer_status(rendered.updated),
                captured_from="openclaw-sessions-jsonl",
                updated=rendered.updated,
                raw_text=_render_session_log(
                    harness="openclaw",
                    session_id=session_id,
                    updated=rendered.updated,
                    metadata={
                        "agent_id": agent_id,
                        "cwd": rendered.cwd,
                        "git_branch": rendered.git_branch,
                        "source_path": str(path),
                    },
                    turns=rendered.turns,
                ),
            )
            captures.append(CollectedSession(capture=capture, source_key=source_key, source_version=source_version))
        return tuple(captures)


@dataclass(frozen=True, slots=True)
class HermesSessionsCollector:
    root: Path
    state_db_path: Path | None = None
    name: str = "hermes"

    def collect(self, *, device: str, state: CollectorState) -> tuple[CollectedSession, ...]:
        captures: list[CollectedSession] = []
        if not self.root.exists():
            return ()
        for path in sorted(self.root.rglob("*.jsonl")):
            source_key = f"hermes:{path}"
            source_version = _stat_version(path)
            if state.version_for(source_key) == source_version:
                continue
            rendered = _parse_hermes_jsonl(path)
            if rendered is None:
                continue
            session_id = rendered.session_id or path.stem
            target_path = _target_path(agent="hermes", device=device, updated=rendered.updated, session_id=session_id)
            metadata = {
                "cwd": rendered.cwd,
                "source_path": str(path),
            }
            if self.state_db_path is not None and self.state_db_path.exists():
                metadata["state_db_path"] = str(self.state_db_path)
            capture = SessionCapture(
                path=target_path,
                agent="hermes",
                device=device,
                session_id=session_id,
                status=_infer_status(rendered.updated),
                captured_from="hermes-sessions-jsonl",
                updated=rendered.updated,
                raw_text=_render_session_log(
                    harness="hermes",
                    session_id=session_id,
                    updated=rendered.updated,
                    metadata=metadata,
                    turns=rendered.turns,
                ),
            )
            captures.append(CollectedSession(capture=capture, source_key=source_key, source_version=source_version))
        return tuple(captures)


@dataclass(frozen=True, slots=True)
class _RenderedSession:
    session_id: str
    updated: str
    turns: tuple[SessionTurn, ...]
    cwd: str = ""
    git_branch: str = ""
    agent_nickname: str = ""
    agent_role: str = ""


def build_collectors(
    harnesses: tuple[str, ...],
    *,
    claude_projects_root: Path | None = None,
    codex_sessions_root: Path | None = None,
    opencode_db_path: Path | None = None,
    openclaw_agents_root: Path | None = None,
    hermes_sessions_root: Path | None = None,
    hermes_state_db_path: Path | None = None,
) -> tuple[SessionCollector, ...]:
    collectors: list[SessionCollector] = []
    for harness in harnesses:
        match harness:
            case "claude":
                collectors.append(
                    ClaudeProjectsCollector(
                        root=claude_projects_root
                        or _env_path("DORY_CLAUDE_PROJECTS_ROOT", Path.home() / ".claude" / "projects")
                    )
                )
            case "codex":
                collectors.append(
                    CodexSessionsCollector(
                        root=codex_sessions_root
                        or _env_path("DORY_CODEX_SESSIONS_ROOT", Path.home() / ".codex" / "sessions")
                    )
                )
            case "opencode":
                collectors.append(
                    OpenCodeCollector(
                        db_path=opencode_db_path
                        or _env_path(
                            "DORY_OPENCODE_DB_PATH",
                            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
                        )
                    )
                )
            case "openclaw":
                collectors.append(
                    OpenClawSessionsCollector(
                        root=openclaw_agents_root
                        or Path(
                            os.environ.get("DORY_OPENCLAW_AGENTS_ROOT")
                            or os.environ.get("DORY_OPENCLAW_SESSIONS_ROOT")
                            or (Path.home() / ".openclaw" / "agents")
                        )
                    )
                )
            case "hermes":
                collectors.append(
                    HermesSessionsCollector(
                        root=hermes_sessions_root
                        or _env_path("DORY_HERMES_SESSIONS_ROOT", Path.home() / ".hermes" / "sessions"),
                        state_db_path=hermes_state_db_path
                        or _env_path("DORY_HERMES_STATE_DB_PATH", Path.home() / ".hermes" / "state.db"),
                    )
                )
            case _:
                raise ValueError(f"unsupported session collector harness: {harness}")
    return tuple(collectors)


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def collect_sessions(
    collectors: tuple[SessionCollector, ...],
    *,
    device: str,
    state: CollectorState,
) -> tuple[CollectedSession, ...]:
    captures: list[CollectedSession] = []
    for collector in collectors:
        captures.extend(collector.collect(device=device, state=state))
    return tuple(captures)


def _parse_claude_jsonl(path: Path) -> _RenderedSession | None:
    turns: list[SessionTurn] = []
    session_id = ""
    updated = ""
    cwd = ""
    git_branch = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        updated = _pick_latest_iso(updated, str(payload.get("timestamp", "")))
        session_id = session_id or str(payload.get("sessionId", "")) or str(payload.get("session_id", ""))
        cwd = cwd or str(payload.get("cwd", ""))
        git_branch = git_branch or str(payload.get("gitBranch", ""))
        entry_type = str(payload.get("type", ""))
        if entry_type == "user":
            message = payload.get("message", {})
            text = _extract_claude_message_text(message, expected_role="user")
            if text:
                turns.append(SessionTurn(speaker="user", text=text))
        elif entry_type == "assistant":
            message = payload.get("message", {})
            text = _extract_claude_message_text(message, expected_role="assistant")
            if text:
                turns.append(SessionTurn(speaker="assistant", text=text))

    if not turns:
        return None
    return _RenderedSession(
        session_id=session_id or path.stem,
        updated=updated or _iso_from_stat(path),
        turns=tuple(turns),
        cwd=cwd,
        git_branch=git_branch,
    )


def _extract_claude_message_text(message: Any, *, expected_role: str) -> str:
    if not isinstance(message, dict):
        return ""
    if str(message.get("role", "")) != expected_role:
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = str(item.get("text", "")).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _parse_codex_jsonl(path: Path) -> _RenderedSession | None:
    turns: list[SessionTurn] = []
    session_id = ""
    updated = ""
    cwd = ""
    agent_nickname = ""
    agent_role = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        updated = _pick_latest_iso(updated, str(payload.get("timestamp", "")))
        entry_type = str(payload.get("type", ""))
        entry_payload = payload.get("payload", {})

        if entry_type == "session_meta" and isinstance(entry_payload, dict):
            session_id = session_id or str(entry_payload.get("id", ""))
            cwd = cwd or str(entry_payload.get("cwd", ""))
            agent_nickname = agent_nickname or str(entry_payload.get("agent_nickname", ""))
            agent_role = agent_role or str(entry_payload.get("agent_role", ""))
            continue

        if not isinstance(entry_payload, dict):
            continue

        payload_type = str(entry_payload.get("type", ""))
        if entry_type == "event_msg" and payload_type == "user_message":
            text = str(entry_payload.get("message", "")).strip()
            if text:
                turns.append(SessionTurn(speaker="user", text=text))
        elif entry_type == "event_msg" and payload_type == "agent_message":
            text = str(entry_payload.get("message", "")).strip()
            if text:
                turns.append(SessionTurn(speaker="assistant", text=text))
        elif entry_type == "response_item" and payload_type == "message":
            if str(entry_payload.get("role", "")) != "assistant":
                continue
            text = _extract_codex_output_text(entry_payload.get("content", []))
            if text:
                turns.append(SessionTurn(speaker="assistant", text=text))

    if not turns:
        return None
    return _RenderedSession(
        session_id=session_id or path.stem,
        updated=updated or _iso_from_stat(path),
        turns=_dedupe_adjacent_turns(tuple(turns)),
        cwd=cwd,
        agent_nickname=agent_nickname,
        agent_role=agent_role,
    )


def _parse_openclaw_jsonl(path: Path) -> _RenderedSession | None:
    turns: list[SessionTurn] = []
    session_id = ""
    updated = ""
    cwd = ""
    git_branch = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        updated = _pick_latest_iso(updated, _extract_timestamp(payload))
        session_id = session_id or _first_non_empty_str(payload.get("sessionId"), payload.get("session_id"))
        cwd = cwd or _first_non_empty_str(payload.get("cwd"), payload.get("directory"))
        git_branch = git_branch or _first_non_empty_str(payload.get("gitBranch"), payload.get("git_branch"))
        role = _extract_role(payload)
        if role is None:
            continue
        text = _extract_generic_message_text(payload)
        if text:
            turns.append(SessionTurn(speaker=role, text=text))

    if not turns:
        return None
    return _RenderedSession(
        session_id=session_id or path.stem,
        updated=updated or _iso_from_stat(path),
        turns=_dedupe_adjacent_turns(tuple(turns)),
        cwd=cwd,
        git_branch=git_branch,
    )


def _parse_hermes_jsonl(path: Path) -> _RenderedSession | None:
    turns: list[SessionTurn] = []
    session_id = ""
    updated = ""
    cwd = ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        updated = _pick_latest_iso(updated, _extract_timestamp(payload))
        session_id = session_id or _first_non_empty_str(payload.get("sessionId"), payload.get("session_id"))
        cwd = cwd or _first_non_empty_str(payload.get("cwd"), payload.get("directory"))
        role = _extract_role(payload)
        if role is None:
            continue
        text = _extract_generic_message_text(payload)
        if text:
            turns.append(SessionTurn(speaker=role, text=text))

    if not turns:
        return None
    return _RenderedSession(
        session_id=session_id or path.stem,
        updated=updated or _iso_from_stat(path),
        turns=_dedupe_adjacent_turns(tuple(turns)),
        cwd=cwd,
    )


def _extract_codex_output_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "output_text":
            continue
        text = str(item.get("text", "")).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _load_opencode_turns(connection: sqlite3.Connection, session_id: str) -> tuple[SessionTurn, ...]:
    message_rows = connection.execute(
        """
        SELECT id, data
        FROM message
        WHERE session_id = ?
        ORDER BY time_created ASC
        """,
        (session_id,),
    ).fetchall()
    turns: list[SessionTurn] = []
    for message_id, raw_data in message_rows:
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            continue
        role = str(payload.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        part_rows = connection.execute(
            """
            SELECT data
            FROM part
            WHERE message_id = ?
            ORDER BY time_created ASC
            """,
            (message_id,),
        ).fetchall()
        parts: list[str] = []
        for (raw_part,) in part_rows:
            try:
                part_payload = json.loads(raw_part)
            except json.JSONDecodeError:
                continue
            if str(part_payload.get("type", "")) != "text":
                continue
            text = str(part_payload.get("text", "")).strip()
            if text:
                parts.append(text)
        if parts:
            turns.append(SessionTurn(speaker=role, text="\n\n".join(parts)))
    return _dedupe_adjacent_turns(tuple(turns))


def _iter_openclaw_session_files(root: Path) -> tuple[Path, ...]:
    if root.name == "sessions":
        return tuple(sorted(root.glob("*.jsonl")))
    return tuple(sorted(root.glob("*/sessions/*.jsonl")))


def _extract_timestamp(payload: dict[str, Any]) -> str:
    direct = _first_non_empty_str(
        payload.get("timestamp"),
        payload.get("time"),
        payload.get("created_at"),
        payload.get("updated_at"),
    )
    if direct:
        return direct
    meta = payload.get("meta")
    if isinstance(meta, dict):
        return _first_non_empty_str(
            meta.get("timestamp"),
            meta.get("time"),
            meta.get("created_at"),
            meta.get("updated_at"),
        )
    return ""


def _extract_role(payload: dict[str, Any]) -> SessionSpeaker | None:
    direct_role = str(payload.get("role", "")).strip().lower()
    if direct_role in {"user", "assistant"}:
        return direct_role  # type: ignore[return-value]

    message = payload.get("message")
    if isinstance(message, dict):
        message_role = str(message.get("role", "")).strip().lower()
        if message_role in {"user", "assistant"}:
            return message_role  # type: ignore[return-value]

    payload_type = str(payload.get("type", "")).strip().lower()
    if payload_type.startswith("user"):
        return "user"
    if payload_type.startswith("assistant"):
        return "assistant"

    return None


def _extract_generic_message_text(payload: dict[str, Any]) -> str:
    if _is_noise_payload(payload):
        return ""
    message = payload.get("message")
    if isinstance(message, dict):
        text = _extract_text_fragments(message)
        if text:
            return text
    return _extract_text_fragments(payload)


def _extract_text_fragments(value: Any) -> str:
    fragments = _collect_text_fragments(value)
    cleaned = [fragment.strip() for fragment in fragments if fragment.strip()]
    return "\n\n".join(cleaned).strip()


def _collect_text_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_collect_text_fragments(item))
        return fragments
    if not isinstance(value, dict):
        return []
    if _is_noise_payload(value):
        return []

    fragments: list[str] = []
    text_value = value.get("text")
    if isinstance(text_value, str):
        fragments.append(text_value)
    for key in ("content", "parts", "message", "messages", "output"):
        if key in value:
            fragments.extend(_collect_text_fragments(value[key]))
    return fragments


def _is_noise_payload(payload: dict[str, Any]) -> bool:
    payload_type = str(payload.get("type", "")).strip().lower()
    if payload_type in {
        "tool",
        "tool_call",
        "tool_use",
        "tool_result",
        "tool_output",
        "function_call",
        "reasoning",
        "thinking",
        "system",
        "metadata",
    }:
        return True
    if payload.get("role") == "system":
        return True
    return False


def _first_non_empty_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _render_session_log(
    *,
    harness: str,
    session_id: str,
    updated: str,
    metadata: dict[str, str],
    turns: tuple[SessionTurn, ...],
) -> str:
    lines = [
        f"Harness: {harness}",
        f"Session ID: {session_id}",
        f"Updated: {updated}",
    ]
    for key, value in metadata.items():
        cleaned = value.strip()
        if not cleaned:
            continue
        label = key.replace("_", " ").title()
        lines.append(f"{label}: {cleaned}")
    lines.append("")
    lines.append("---")
    lines.append("")
    for turn in turns:
        prefix = "User" if turn.speaker == "user" else "Assistant"
        lines.append(f"{prefix}:")
        lines.append(turn.text.strip())
        lines.append("")
    return "\n".join(lines).strip()


def _target_path(*, agent: str, device: str, updated: str, session_id: str) -> str:
    date_prefix = updated[:10] if len(updated) >= 10 else "unknown-date"
    return f"logs/sessions/{agent}/{_slugify(device)}/{date_prefix}-{_slugify(session_id)}.md"


def _stat_version(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _iso_from_stat(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _iso_from_millis(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _pick_latest_iso(current: str, candidate: str) -> str:
    if not candidate:
        return current
    if not current:
        return candidate
    return candidate if candidate > current else current


def _dedupe_adjacent_turns(turns: tuple[SessionTurn, ...]) -> tuple[SessionTurn, ...]:
    deduped: list[SessionTurn] = []
    for turn in turns:
        if deduped and deduped[-1].speaker == turn.speaker and deduped[-1].text == turn.text:
            continue
        deduped.append(turn)
    return tuple(deduped)


def _infer_status(updated: str) -> str:
    try:
        updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return "active"
    delta_seconds = (datetime.now(tz=UTC) - updated_at.astimezone(UTC)).total_seconds()
    return "done" if delta_seconds > 1800 else "active"


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    cleaned = [character if character.isalnum() or character == "-" else "-" for character in lowered]
    slug = "".join(cleaned).strip("-")
    return slug or "session"
