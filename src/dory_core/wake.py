from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.profiles import ProfileRegistry
from dory_core.slug import slugify_path_segment
from dory_core.token_counting import TokenCounter, build_token_counter
from dory_core.types import WakeProfile, WakeReq, WakeResp

_CORE_SECTION_PATHS = {
    "active": Path("core/active.md"),
    "defaults": Path("core/defaults.md"),
    "env": Path("core/env.md"),
    "identity": Path("core/identity.md"),
    "soul": Path("core/soul.md"),
    "user": Path("core/user.md"),
    "writing_voice": Path("knowledge/personal/writing-voice.md"),
}
_CORE_SECTION_NAMES = set(_CORE_SECTION_PATHS)


@dataclass(frozen=True, slots=True)
class HotBlockSection:
    path: Path
    content: str


class WakeBuilder:
    def __init__(self, root: Path = Path("."), *, token_counter: TokenCounter | None = None) -> None:
        self.root = Path(root)
        self.token_counter = token_counter or build_token_counter()
        self.profile_registry = ProfileRegistry(self.root)

    def build(self, req: WakeReq) -> WakeResp:
        sections = self._load_hot_block_sections(profile=req.profile, agent=req.agent)
        project_section = self._load_project_section(req.project, profile=req.profile, agent=req.agent)
        if project_section is not None:
            sections.append(project_section)
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
        # Profiles keep wake deterministic while letting agents spend their
        # startup budget on task-specific context first.
        for name in self.profile_registry.wake_profile(profile).sections:
            section = self._load_named_section(name=name, profile=profile, agent=agent)
            if section is None:
                continue
            sections.append(section)
        return sections

    def _load_named_section(self, *, name: str, profile: WakeProfile, agent: str) -> HotBlockSection | None:
        if name == "privacy_boundaries":
            return self._load_privacy_boundaries_section(agent=agent)
        rel_path = _resolve_wake_section_path(name)
        if rel_path is None:
            return None
        path = self.root / rel_path
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
        if not _is_within_root(path, self.root):
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
        section_budget = self.profile_registry.wake_profile(profile).section_budgets.get(_section_budget_key(name))
        if section_budget is None:
            section_budget = self.profile_registry.wake_profile(profile).section_budgets.get(name)
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
        resolved_section_path = _resolve_wake_section_path(name)
        section_path = resolved_section_path.as_posix() if resolved_section_path is not None else name
        if name in _CORE_SECTION_NAMES:
            suffix = f"<!-- wake excerpt truncated; use dory_get('{section_path}') for the full file -->"
        else:
            suffix = "<!-- wake excerpt truncated; use dory_get on the source path for the full file -->"
        return f"{excerpt}\n\n{suffix}".strip()

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

    def _load_project_section(
        self,
        project: str | None,
        *,
        profile: WakeProfile,
        agent: str,
    ) -> HotBlockSection | None:
        if project is None or not project.strip():
            return None
        path = self._resolve_project_path(project)
        if path is None:
            return None
        return self._load_file_section(path, name="project", profile=profile, agent=agent)

    def _resolve_project_path(self, project: str) -> Path | None:
        projects_root = self.root / "projects"
        if not projects_root.exists():
            return None

        normalized = project.strip()
        if normalized.startswith("project:"):
            normalized = normalized.split(":", 1)[1]

        direct = projects_root / slugify_path_segment(normalized) / "state.md"
        if direct.exists():
            return direct

        wanted = _normalize_project_lookup_value(normalized)
        for path in sorted(projects_root.glob("*/state.md")):
            text = path.read_text(encoding="utf-8").strip()
            try:
                document = load_markdown_document(text)
            except ValueError:
                continue
            values = [
                str(document.frontmatter.get("title", "")),
                str(document.frontmatter.get("slug", "")),
                path.parent.name,
            ]
            aliases = document.frontmatter.get("aliases", [])
            if isinstance(aliases, list):
                values.extend(str(alias) for alias in aliases)
            if wanted in {_normalize_project_lookup_value(value) for value in values if value.strip()}:
                return path

        return None

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


def _resolve_wake_section_path(name: str) -> Path | None:
    if name in _CORE_SECTION_PATHS:
        return _CORE_SECTION_PATHS[name]
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _section_budget_key(name: str) -> str:
    path = Path(name)
    if name in _CORE_SECTION_NAMES or name == "privacy_boundaries":
        return name
    for alias, section_path in _CORE_SECTION_PATHS.items():
        if path.as_posix() == section_path.as_posix():
            return alias
    if path.suffix == ".md":
        return path.stem
    return path.as_posix()


def _normalize_project_lookup_value(value: str) -> str:
    return slugify_path_segment(value.removeprefix("project:").strip())
