from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.token_counting import TokenCounter, build_token_counter
from dory_core.types import WakeProfile, WakeReq, WakeResp

_WAKE_SECTION_ORDERS: dict[WakeProfile, tuple[str, ...]] = {
    "default": ("user", "soul", "env", "active", "identity", "defaults"),
    "casual": ("user", "soul", "identity", "defaults", "active", "env"),
    "coding": ("active", "env", "defaults"),
    "writing": ("soul", "writing_voice", "defaults", "active"),
    "privacy": ("privacy_boundaries", "defaults", "soul"),
}
_WAKE_PROFILE_SECTION_BUDGETS: dict[WakeProfile, dict[str, int]] = {
    "coding": {
        "active": 480,
        "env": 340,
        "defaults": 260,
        "user": 220,
        "soul": 220,
        "identity": 180,
    },
    "writing": {
        "soul": 520,
        "writing_voice": 420,
        "defaults": 180,
        "active": 180,
    },
    "privacy": {
        "privacy_boundaries": 420,
        "defaults": 260,
        "soul": 180,
    },
}
_CORE_SECTION_NAMES = {"active", "defaults", "env", "identity", "soul", "user"}


@dataclass(frozen=True, slots=True)
class HotBlockSection:
    path: Path
    content: str


class WakeBuilder:
    def __init__(self, root: Path = Path("."), *, token_counter: TokenCounter | None = None) -> None:
        self.root = Path(root)
        self.token_counter = token_counter or build_token_counter()

    def build(self, req: WakeReq) -> WakeResp:
        sections = self._load_hot_block_sections(profile=req.profile, agent=req.agent)
        if req.include_pinned_decisions:
            sections.extend(self._load_pinned_decisions())
        recent_sessions = self._load_recent_sessions(req.include_recent_sessions)
        block, sources = self._assemble_block(sections, req.budget_tokens, agent=req.agent)
        if recent_sessions:
            block, sources = self._append_recent_sessions(
                block,
                sources,
                recent_sessions,
                req.budget_tokens,
                agent=req.agent,
            )

        return WakeResp(
            profile=req.profile,
            tokens_estimated=self._count_tokens(block, agent=req.agent),
            block=block,
            sources=sources,
            frozen_at=datetime.now(tz=UTC),
        )

    def _load_hot_block_sections(self, *, profile: WakeProfile = "default", agent: str) -> list[HotBlockSection]:
        sections: list[HotBlockSection] = []
        # Profiles keep wake deterministic while letting coding agents spend
        # their small startup budget on operational context first.
        for name in _WAKE_SECTION_ORDERS.get(profile, _WAKE_SECTION_ORDERS["default"]):
            section = self._load_named_section(name=name, profile=profile, agent=agent)
            if section is None:
                continue
            sections.append(section)
        return sections

    def _load_named_section(self, *, name: str, profile: WakeProfile, agent: str) -> HotBlockSection | None:
        if name == "writing_voice":
            path = self.root / "knowledge" / "personal" / "dee-writing-voice.md"
            return self._load_file_section(path, name=name, profile=profile, agent=agent)
        if name == "privacy_boundaries":
            return self._load_privacy_boundaries_section(agent=agent)
        if name not in _CORE_SECTION_NAMES:
            return None
        path = self.root / "core" / f"{name}.md"
        return self._load_file_section(path, name=name, profile=profile, agent=agent)

    def _load_file_section(
        self,
        path: Path,
        *,
        name: str,
        profile: WakeProfile,
        agent: str,
    ) -> HotBlockSection | None:
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8").strip()
        return HotBlockSection(
            path=path.relative_to(self.root),
            content=self._compact_profile_section(
                name=name,
                content=content,
                profile=profile,
                agent=agent,
            ),
        )

    def _load_privacy_boundaries_section(self, *, agent: str) -> HotBlockSection | None:
        candidates = (self.root / "core" / "user.md", self.root / "core" / "identity.md")
        boundary_lines: list[str] = []
        for path in candidates:
            if not path.exists():
                continue
            boundary_lines.extend(_extract_privacy_boundary_lines(path.read_text(encoding="utf-8")))
        if not boundary_lines:
            return None
        content = "# Privacy Boundaries\n\n" + "\n".join(_dedupe_preserve_order(boundary_lines))
        return HotBlockSection(
            path=Path("core/user.md"),
            content=self._compact_profile_section(
                name="privacy_boundaries",
                content=content,
                profile="privacy",
                agent=agent,
            ),
        )

    def _compact_profile_section(
        self,
        *,
        name: str,
        content: str,
        profile: WakeProfile,
        agent: str,
    ) -> str:
        section_budget = _WAKE_PROFILE_SECTION_BUDGETS.get(profile, {}).get(name)
        if section_budget is None or self._count_tokens(content, agent=agent) <= section_budget:
            return content

        lines: list[str] = []
        for line in content.splitlines():
            candidate = "\n".join([*lines, line]).strip()
            if not candidate:
                lines.append(line)
                continue
            if self._count_tokens(candidate, agent=agent) > section_budget:
                break
            lines.append(line)

        excerpt = "\n".join(lines).strip()
        if not excerpt:
            return content
        return f"{excerpt}\n\n<!-- wake excerpt truncated; use dory_get('core/{name}.md') for the full file -->"

    def _load_recent_sessions(self, limit: int) -> list[HotBlockSection]:
        if limit <= 0:
            return []

        sessions_root = self._resolve_sessions_root()
        if sessions_root is None:
            return []

        session_paths = sorted(
            sessions_root.rglob("*.md"),
            key=lambda path: (path.stat().st_mtime, path.as_posix()),
            reverse=True,
        )[:limit]
        sections: list[HotBlockSection] = []
        for path in session_paths:
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            sections.append(
                HotBlockSection(
                    path=path.relative_to(self.root),
                    content=text,
                )
            )
        return sections

    def _resolve_sessions_root(self) -> Path | None:
        preferred = self.root / "logs" / "sessions"
        if preferred.exists():
            return preferred

        legacy = self.root / "sessions"
        if legacy.exists():
            return legacy

        return None

    def _load_pinned_decisions(self) -> list[HotBlockSection]:
        sections: list[HotBlockSection] = []
        candidates = [self.root / "decisions" / "canonical", self.root / "decisions"]
        decision_root = next((path for path in candidates if path.exists()), None)
        if decision_root is None:
            return sections

        pinned: list[tuple[str, str, Path, str]] = []
        for path in sorted(decision_root.glob("*.md")):
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            try:
                document = load_markdown_document(text)
            except ValueError:
                continue
            if document.frontmatter.get("pinned") is not True:
                continue
            status = str(document.frontmatter.get("status", "")).strip().lower()
            if status in {"retired", "superseded", "stale"}:
                continue
            updated = str(document.frontmatter.get("updated", "") or document.frontmatter.get("created", "") or "")
            pinned.append((updated, path.name, path, text))

        for _updated, _name, path, text in sorted(pinned, reverse=True)[:3]:
            sections.append(
                HotBlockSection(
                    path=path.relative_to(self.root),
                    content=text,
                )
            )
        return sections

    def _assemble_block(
        self,
        sections: list[HotBlockSection],
        budget_tokens: int,
        *,
        agent: str,
    ) -> tuple[str, list[str]]:
        rendered_sections: list[str] = []
        sources: list[str] = []
        current_tokens = 0

        for section in sections:
            section_tokens = self._count_tokens(section.content, agent=agent)
            separator_tokens = self._count_tokens("\n\n", agent=agent) if rendered_sections else 0
            if rendered_sections and current_tokens + separator_tokens + section_tokens > budget_tokens:
                break
            rendered_sections.append(section.content)
            sources.append(str(section.path))
            current_tokens += section_tokens + separator_tokens

        return "\n\n".join(rendered_sections), sources

    def _append_recent_sessions(
        self,
        block: str,
        sources: list[str],
        sessions: list[HotBlockSection],
        budget_tokens: int,
        *,
        agent: str,
    ) -> tuple[str, list[str]]:
        heading = "## Recent sessions"
        session_lines: list[str] = []
        appended_sources: list[str] = []

        for session in sessions:
            summary_line = _summarize_session(session)
            candidate_lines = [heading, *session_lines, summary_line]
            candidate = block
            if candidate:
                candidate += "\n\n"
            candidate += "\n".join(candidate_lines)
            if self._count_tokens(candidate, agent=agent) > budget_tokens:
                break
            session_lines.append(summary_line)
            appended_sources.append(str(session.path))

        if not session_lines:
            return block, sources

        rendered = block
        if rendered:
            rendered += "\n\n"
        rendered += "\n".join([heading, *session_lines])
        return rendered, [*sources, *appended_sources]

    def _count_tokens(self, text: str, *, agent: str) -> int:
        return self.token_counter.count(text, agent=agent)


def _summarize_session(section: HotBlockSection) -> str:
    body = section.path.stem
    in_frontmatter = False
    for index, raw_line in enumerate(section.content.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if index == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if line.startswith("#"):
            continue
        body = line
        break
    return f"- {section.path.as_posix()}: {body[:120]}"


def _extract_privacy_boundary_lines(content: str) -> list[str]:
    lines: list[str] = []
    in_frontmatter = False
    in_boundary_section = False
    for index, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()
        lowered = line.casefold()
        if index == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if not line:
            continue
        if line.startswith("#"):
            in_boundary_section = any(
                marker in lowered
                for marker in (
                    "boundar",
                    "private",
                    "privacy",
                    "sensitive",
                    "public",
                    "redact",
                    "do not",
                    "don't",
                    "avoid",
                )
            )
            if in_boundary_section:
                lines.append(line)
            continue
        if not (in_boundary_section or _line_mentions_privacy_boundary(lowered)):
            continue
        if _line_looks_like_personal_identifier(lowered):
            continue
        lines.append(raw_line.rstrip())
    return lines


def _line_mentions_privacy_boundary(lowered_line: str) -> bool:
    return any(
        marker in lowered_line
        for marker in (
            "boundary",
            "boundaries",
            "private",
            "privacy",
            "sensitive",
            "redact",
            "do not mention",
            "don't mention",
            "do not share",
            "avoid sharing",
            "public-safe",
            "public safe",
        )
    )


def _line_looks_like_personal_identifier(lowered_line: str) -> bool:
    return any(
        marker in lowered_line
        for marker in (
            "telegram",
            "email",
            "phone",
            "dob",
            "birth",
            "birthday",
            "passport",
            "ssn",
            "address",
            "orcid",
        )
    )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
