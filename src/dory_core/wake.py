from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dory_core.compiled_wiki import (
    collect_general_cards,
    collect_project_card,
    wake_card_excerpt,
    wake_staleness_note,
)
from dory_core.frontmatter import load_markdown_document
from dory_core.hot_context import HotContextPacket, SourceBackedItem
from dory_core.maintenance import wake_maintenance_summary
from dory_core.profiles import ProfileRegistry
from dory_core.project_context import resolve_project_context, resolve_project_handle, resolve_project_path
from dory_core.token_counting import TokenCounter, build_token_counter
from dory_core.types import WakeProfile, WakeReq, WakeResp

_CORE_SECTION_PATHS = {
    "active": Path("core/active.md"),
    "coding": Path("core/coding.md"),
    "defaults": Path("core/defaults.md"),
    "env": Path("core/env.md"),
    "identity": Path("core/identity.md"),
    "soul": Path("core/soul.md"),
    "user": Path("core/user.md"),
    "writing_voice": Path("knowledge/personal/writing-voice.md"),
}
_CORE_SECTION_NAMES = set(_CORE_SECTION_PATHS)

# Hot core pages claim to be current; flag them when they sit untouched past
# their freshness SLA. Compiled cards and project state get a looser window.
_CORE_STALE_AFTER_DAYS = 21
_CARD_STALE_AFTER_DAYS = 30


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
        project_context = resolve_project_context(project=req.project, cwd=req.cwd, root=self.root)
        project_handle = resolve_project_handle(project=req.project, cwd=req.cwd, root=self.root)
        project_signal = _has_project_signal(req.project, req.cwd)
        unresolved_coding_project = req.profile == "coding" and project_signal and project_context is None
        warnings: list[str] = []
        if unresolved_coding_project:
            warnings.append(
                "Project or cwd did not resolve to a known project; coding wake skipped global active context."
            )

        # L0: Compiled project card — highest-priority context
        sections = self._collect_project_card(project=project_handle, profile=req.profile)

        # L1: Raw project state right after the compiled project card
        project_section = self._load_project_section(
            project_handle,
            profile=req.profile,
            agent=req.agent,
            warnings=warnings,
        )
        if project_section is not None:
            sections.append(project_section)

        # L2: Profile hot sections
        profile_sections = self._load_hot_block_sections(profile=req.profile, agent=req.agent, warnings=warnings)
        if unresolved_coding_project:
            profile_sections = _without_global_active(profile_sections)
        elif project_context is not None:
            profile_sections = _without_global_active(profile_sections)
        sections.extend(profile_sections)

        if req.include_pinned_decisions:
            sections.extend(self._load_pinned_decisions())

        # L3: General compiled cards only fill budget left after core context
        sections.extend(self._collect_general_cards(profile=req.profile))
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
            warnings=warnings,
        )

    def build_packet(self, req: WakeReq) -> HotContextPacket:
        """Build wake context as the shared internal hot-context packet."""
        wake = self.build(req)
        guardrails = ("privacy boundaries active",) if wake.profile == "privacy" else ()
        return HotContextPacket(
            profile=wake.profile,
            guardrails=guardrails,
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=tuple(wake.sources),
            warnings=tuple(wake.warnings),
            partial=bool(wake.warnings),
            wake_context=(SourceBackedItem(text=wake.block),) if wake.block else (),
        )

    def _load_hot_block_sections(
        self,
        *,
        profile: WakeProfile = "default",
        agent: str,
        warnings: list[str],
    ) -> list[HotBlockSection]:
        sections: list[HotBlockSection] = []
        # Profiles keep wake deterministic while letting agents spend their
        # startup budget on task-specific context first.
        for name in self.profile_registry.wake_profile(profile).sections:
            section = self._load_named_section(name=name, profile=profile, agent=agent, warnings=warnings)
            if section is None:
                continue
            sections.append(section)
        return sections

    def _load_named_section(
        self,
        *,
        name: str,
        profile: WakeProfile,
        agent: str,
        warnings: list[str],
    ) -> HotBlockSection | None:
        if name == "privacy_boundaries":
            return self._load_privacy_boundaries_section(agent=agent)
        if name == "maintenance":
            return self._load_maintenance_section(profile=profile, agent=agent)
        rel_path = _resolve_wake_section_path(name)
        if rel_path is None:
            return None
        path = self.root / rel_path
        return self._load_file_section(path, name=name, profile=profile, agent=agent, warnings=warnings)

    def _load_file_section(
        self,
        path: Path,
        *,
        name: str,
        profile: WakeProfile,
        agent: str,
        warnings: list[str],
    ) -> HotBlockSection | None:
        if not path.exists():
            return None
        if not _is_within_root(path, self.root):
            return None
        content = path.read_text(encoding="utf-8").strip()
        rel_path = path.relative_to(self.root)
        denied_reason = _wake_source_denial(
            rel_path=rel_path,
            content=content,
            profile=profile,
            profile_registry=self.profile_registry,
        )
        if denied_reason is not None:
            warnings.append(f"Wake source skipped {rel_path.as_posix()}: {denied_reason}.")
            return None
        is_core_section = _section_budget_key(name) in _CORE_SECTION_NAMES
        if is_core_section or name == "project":
            max_age = _CORE_STALE_AFTER_DAYS if is_core_section else _CARD_STALE_AFTER_DAYS
            note = wake_staleness_note(content, max_age_days=max_age)
            if note is not None:
                content = _insert_note_after_frontmatter(content, note)
        return HotBlockSection(
            path=rel_path,
            content=self._compact_profile_section(
                name=name,
                content=content,
                profile=profile,
                agent=agent,
            ),
        )

    def _load_maintenance_section(self, *, profile: WakeProfile, agent: str) -> HotBlockSection | None:
        lines = wake_maintenance_summary(
            self.root,
            core_stale_after_days=_CORE_STALE_AFTER_DAYS,
            card_stale_after_days=_CARD_STALE_AFTER_DAYS,
        )
        if not lines:
            return None
        content = "# Corpus Maintenance\n\n" + "\n".join(lines)
        return HotBlockSection(
            path=Path("maintenance"),
            content=self._compact_profile_section(
                name="maintenance",
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
        )
        sections: list[HotBlockSection] = []
        for path in session_paths:
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            if not _is_recent_session_wake_candidate(text):
                continue
            sections.append(
                HotBlockSection(
                    path=path.relative_to(self.root),
                    content=text,
                )
            )
            if len(sections) >= limit:
                break
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
        warnings: list[str],
    ) -> HotBlockSection | None:
        if project is None or not project.strip():
            return None
        path = resolve_project_path(self.root, project)
        if path is None:
            return None
        return self._load_file_section(path, name="project", profile=profile, agent=agent, warnings=warnings)

    def _collect_project_card(
        self,
        *,
        project: str | None = None,
        profile: WakeProfile = "default",
    ) -> list[HotBlockSection]:
        raw = collect_project_card(
            self.root,
            project=project,
            retrieval_profile=self.profile_registry.retrieval_profile(profile),
        )
        sections: list[HotBlockSection] = []
        for rel, content in raw:
            excerpt = wake_card_excerpt(content)
            note = wake_staleness_note(content, max_age_days=_CARD_STALE_AFTER_DAYS)
            if note is not None:
                excerpt = _insert_note_after_heading(excerpt, note)
            sections.append(HotBlockSection(path=rel, content=excerpt))
        return sections

    def _collect_general_cards(self, *, profile: WakeProfile) -> list[HotBlockSection]:
        raw = collect_general_cards(
            self.root,
            retrieval_profile=self.profile_registry.retrieval_profile(profile),
            max_cards=_general_compiled_card_limit(profile),
        )
        return [HotBlockSection(path=rel, content=wake_card_excerpt(content)) for rel, content in raw]

    def _assemble_block(
        self,
        sections: list[HotBlockSection],
        budget_tokens: int,
        *,
        agent: str,
    ) -> tuple[str, list[str]]:
        rendered_sections: list[str] = []
        sources: list[str] = []
        seen_sources: set[Path] = set()
        current_tokens = 0

        for section in sections:
            if section.path in seen_sources:
                continue
            content = section.content
            section_tokens = self._count_tokens(content, agent=agent)
            if not rendered_sections and section_tokens > budget_tokens:
                content = self._fit_section_to_budget(content, budget_tokens, agent=agent)
                section_tokens = self._count_tokens(content, agent=agent)
            separator_tokens = self._count_tokens("\n\n", agent=agent) if rendered_sections else 0
            if rendered_sections and current_tokens + separator_tokens + section_tokens > budget_tokens:
                break
            rendered_sections.append(content)
            sources.append(str(section.path))
            seen_sources.add(section.path)
            current_tokens += section_tokens + separator_tokens

        return "\n\n".join(rendered_sections), sources

    def _fit_section_to_budget(self, content: str, budget_tokens: int, *, agent: str) -> str:
        lines: list[str] = []
        suffix = "<!-- wake section truncated to fit requested budget -->"
        content = _visible_truncation_content(content)
        suffix_tokens = self._count_tokens("\n\n" + suffix, agent=agent)
        line_budget = max(1, budget_tokens - suffix_tokens)
        for line in content.splitlines():
            candidate = "\n".join([*lines, line]).strip()
            if not candidate:
                lines.append(line)
                continue
            if self._count_tokens(candidate, agent=agent) > line_budget:
                break
            lines.append(line)
        excerpt = "\n".join(lines).strip()
        if not excerpt:
            return suffix
        return f"{excerpt}\n\n{suffix}".strip()

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


def _wake_source_denial(
    *,
    rel_path: Path,
    content: str,
    profile: WakeProfile,
    profile_registry: ProfileRegistry,
) -> str | None:
    rel = rel_path.as_posix()
    retrieval_profile = profile_registry.retrieval_profile(profile)
    if not retrieval_profile.allows_path(rel, corpus="durable"):
        return "denied by active profile source policy"

    if rel_path.parts[:1] in (( "inbox",), ("archive",)):
        return "inbox and archive files are not wake-eligible"
    if rel.startswith("logs/sessions/") or rel.startswith("sessions/"):
        return "raw session files are not wake-eligible"

    frontmatter = _safe_frontmatter(content)
    if not frontmatter:
        return None

    status = str(frontmatter.get("status", "") or "").strip().casefold()
    if status in {"retired", "superseded", "stale", "quarantined", "quarantine", "raw"}:
        return f"status {status!r} is not wake-eligible"

    temperature = str(frontmatter.get("temperature", "") or "").strip().casefold()
    if temperature == "cold":
        return "cold memory is not wake-eligible"

    source_kind = str(frontmatter.get("source_kind", "") or "").strip().casefold()
    if source_kind in {"raw", "session", "imported"}:
        return f"source_kind {source_kind!r} is not wake-eligible"
    if source_kind == "generated" and not rel.startswith("wiki/"):
        return "generated non-wiki memory is not wake-eligible"

    visibility = str(frontmatter.get("visibility", "") or "").strip().casefold()
    if visibility == "private" and profile in {"coding", "writing", "privacy", "admin"}:
        return f"profile {profile!r} does not load private files at wake"

    sensitivity = str(frontmatter.get("sensitivity", "") or "").strip().casefold()
    if sensitivity in {"credentials", "contact", "financial", "legal", "health"}:
        return f"sensitivity {sensitivity!r} is not wake-eligible"
    if sensitivity == "personal" and profile in {"coding", "privacy", "admin"}:
        return f"profile {profile!r} does not load personal files at wake"

    return None


def _safe_frontmatter(content: str) -> dict[str, object]:
    try:
        return load_markdown_document(content).frontmatter
    except ValueError:
        return {}


def _is_recent_session_wake_candidate(content: str) -> bool:
    frontmatter = _safe_frontmatter(content)
    status = str(frontmatter.get("status", "") or "").strip().casefold()
    if not status:
        return True
    return status in {"done", "complete", "completed"}


def _visible_truncation_content(content: str) -> str:
    try:
        document = load_markdown_document(content)
    except ValueError:
        return content
    title = str(document.frontmatter.get("title", "") or "").strip()
    body = document.body.strip()
    if title and not body.startswith("#"):
        return f"# {title}\n\n{body}".strip()
    return body or (f"# {title}" if title else content)


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


def _insert_note_after_frontmatter(content: str, note: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "\n".join([*lines[: index + 1], "", note, *lines[index + 1 :]])
    return f"{note}\n\n{content}"


def _insert_note_after_heading(content: str, note: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].startswith("#"):
        return "\n".join([lines[0], "", note, *lines[1:]])
    return f"{note}\n\n{content}"


def _general_compiled_card_limit(profile: WakeProfile) -> int:
    # Coding/admin wakes stay operational and writing wakes stay voice-driven;
    # generic person/concept cards belong in search, not their startup block.
    if profile in {"coding", "writing", "admin"}:
        return 0
    return 2


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
            # Bare "public" is not a boundary marker: it would sweep in whole
            # positioning/identity sections that merely describe public framing.
            in_boundary_section = any(
                marker in lowered
                for marker in (
                    "boundar",
                    "private",
                    "privacy",
                    "sensitive",
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
            "out of public",
            "not public",
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


def _has_project_signal(project: str | None, cwd: str | None) -> bool:
    return bool((project or "").strip() or (cwd or "").strip())


def _without_global_active(sections: list[HotBlockSection]) -> list[HotBlockSection]:
    return [section for section in sections if section.path != Path("core/active.md")]
