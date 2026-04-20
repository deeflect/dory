from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from pathlib import Path

from dory_core.claim_store import ClaimStore
from dory_core.frontmatter import load_markdown_document
from dory_core.fs import atomic_write_text


_WIKI_ROOTS = ("wiki",)
_WIKI_FAMILIES = ("people", "projects", "concepts", "decisions", "indexes")
_WIKI_META_FILES = ("hot.md", "index.md", "log.md")


@dataclass(frozen=True, slots=True)
class WikiPageEntry:
    path: Path
    title: str
    summary: str
    updated: str
    family: str


@dataclass(frozen=True, slots=True)
class ClaimHotContext:
    recent_facts: tuple[str, ...]
    recent_changes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WikiIndexBuilder:
    root: Path

    def refresh(self) -> list[str]:
        written: list[str] = []
        claim_context = self._claim_hot_context()
        for wiki_root in self._wiki_roots():
            wiki_root.mkdir(parents=True, exist_ok=True)
            root_rel = wiki_root.relative_to(self.root).as_posix()
            global_links: list[str] = []
            recent_pages: list[WikiPageEntry] = []
            for family in _WIKI_FAMILIES:
                family_root = wiki_root / family
                family_root.mkdir(parents=True, exist_ok=True)
                written.append(self._write_family_index(family_root, root_rel, family))
                global_links.append(f"- [[{root_rel}/{family}/index|{family.title()}]]")
                recent_pages.extend(self._family_entries(family_root, family))

            global_index = wiki_root / "index.md"
            atomic_write_text(
                global_index,
                _render_global_index_page(
                    root_rel=root_rel,
                    family_links=tuple(global_links),
                    recent_pages=tuple(self._sort_recent_pages(recent_pages)[:8]),
                    family_counts=self._family_counts(recent_pages),
                ),
                encoding="utf-8",
            )
            written.append(str(global_index.relative_to(self.root)))
            hot_page = wiki_root / "hot.md"
            claim_context = self._claim_hot_context()
            atomic_write_text(
                hot_page,
                _render_hot_page(
                    current_focus=self._current_focus(claim_context),
                    recent_pages=tuple(self._sort_recent_pages(recent_pages)[:5]),
                    session_lines=self._recent_session_lines(limit=3),
                    claim_context=claim_context,
                ),
                encoding="utf-8",
            )
            written.append(str(hot_page.relative_to(self.root)))
            log_page = wiki_root / "log.md"
            atomic_write_text(
                log_page,
                _render_log_page(
                    claim_context=claim_context,
                    recent_pages=tuple(self._sort_recent_pages(recent_pages)[:10]),
                    session_lines=self._recent_session_lines(limit=5),
                ),
                encoding="utf-8",
            )
            written.append(str(log_page.relative_to(self.root)))
        return written

    def _write_family_index(self, family_root: Path, root_rel: str, family: str) -> str:
        entries = self._sort_recent_pages(self._family_entries(family_root, family))
        links = tuple(f"- [[{root_rel}/{family}/{entry.path.stem}|{entry.title}]]" for entry in entries)
        index_path = family_root / "index.md"
        atomic_write_text(
            index_path,
            _render_index_page(
                title=family.title(),
                summary=f"Compiled wiki index for {family}.",
                links=links,
            ),
            encoding="utf-8",
        )
        return str(index_path.relative_to(self.root))

    def _wiki_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for root_rel in _WIKI_ROOTS:
            roots.append(self.root / root_rel)
        return tuple(roots)

    def _family_entries(self, family_root: Path, family: str) -> list[WikiPageEntry]:
        entries: list[WikiPageEntry] = []
        claim_store = self._load_claim_store()
        for path in sorted(family_root.glob("*.md"), key=_page_sort_key, reverse=True):
            if path.name in {"index.md", *_WIKI_META_FILES}:
                continue
            claim_summary = self._claim_summary(claim_store, family=family, slug=path.stem)
            claim_updated = self._claim_updated(claim_store, family=family, slug=path.stem)
            entries.append(
                WikiPageEntry(
                    path=path,
                    title=_page_label(path),
                    summary=claim_summary or _page_summary(path),
                    updated=claim_updated or _page_updated(path),
                    family=family,
                )
            )
        return entries

    def _sort_recent_pages(self, entries: list[WikiPageEntry]) -> list[WikiPageEntry]:
        return sorted(entries, key=lambda entry: (entry.updated, entry.title.casefold()), reverse=True)

    def _family_counts(self, entries: list[WikiPageEntry]) -> dict[str, int]:
        counts = {family: 0 for family in _WIKI_FAMILIES}
        for entry in entries:
            counts[entry.family] = counts.get(entry.family, 0) + 1
        return counts

    def _current_focus(self, claim_context: ClaimHotContext) -> str:
        active_path = self.root / "core" / "active.md"
        if not active_path.exists():
            return claim_context.recent_facts[0] if claim_context.recent_facts else "No current focus recorded."
        return _page_summary(active_path) or (
            claim_context.recent_facts[0] if claim_context.recent_facts else "No current focus recorded."
        )

    def _recent_session_lines(self, limit: int) -> tuple[str, ...]:
        sessions_root = self.root / "logs" / "sessions"
        if not sessions_root.exists():
            return ()
        session_paths = sorted(
            sessions_root.rglob("*.md"),
            key=lambda path: (path.stat().st_mtime, path.as_posix()),
            reverse=True,
        )[:limit]
        lines: list[str] = []
        for path in session_paths:
            summary = _page_summary(path) or path.stem
            rel_path = path.relative_to(self.root).as_posix()
            lines.append(f"- {rel_path}: {summary}")
        return tuple(lines)

    def _claim_hot_context(self, *, limit: int = 12) -> ClaimHotContext:
        store = self._load_claim_store()
        if store is None:
            return ClaimHotContext(recent_facts=(), recent_changes=())
        active_claims = store.recent_active_claims(limit=limit)
        details = store.recent_event_details(limit=limit)
        recent_facts: list[str] = []
        recent_changes: list[str] = []
        seen_entities: set[str] = set()
        for claim in active_claims:
            statement = claim.statement.strip()
            if not statement or claim.entity_id in seen_entities:
                continue
            recent_facts.append(statement)
            seen_entities.add(claim.entity_id)
        for detail in details:
            statement = detail.statement.strip()
            if statement and detail.entity_id not in seen_entities and detail.event_type == "added":
                recent_facts.append(statement)
                seen_entities.add(detail.entity_id)
            change = _render_claim_change_line(detail)
            if change:
                recent_changes.append(change)
        return ClaimHotContext(
            recent_facts=tuple(_dedupe_strings(recent_facts)[:5]),
            recent_changes=tuple(_dedupe_strings(recent_changes)[:5]),
        )

    def _load_claim_store(self) -> ClaimStore | None:
        claim_store_path = self.root / ".dory" / "claim-store.db"
        if not claim_store_path.exists():
            return None
        return ClaimStore(claim_store_path)

    def _claim_summary(
        self,
        claim_store: ClaimStore | None,
        *,
        family: str,
        slug: str,
    ) -> str:
        entity_id = _entity_id_for_wiki_entry(family, slug)
        if claim_store is None or entity_id is None:
            return ""
        claims = claim_store.current_claims(entity_id)
        for claim in claims:
            statement = claim.statement.strip()
            if statement:
                return statement
        history = claim_store.claim_history(entity_id)
        for claim in reversed(history):
            statement = claim.statement.strip()
            if statement:
                return statement
        return ""

    def _claim_updated(
        self,
        claim_store: ClaimStore | None,
        *,
        family: str,
        slug: str,
    ) -> str:
        entity_id = _entity_id_for_wiki_entry(family, slug)
        if claim_store is None or entity_id is None:
            return ""
        events = claim_store.claim_events(entity_id)
        if events:
            return events[-1].created_at[:10]
        claims = claim_store.claim_history(entity_id)
        if claims:
            return claims[-1].updated_at[:10]
        return ""


def _render_index_page(*, title: str, summary: str, links: tuple[str, ...]) -> str:
    lines = [
        "---",
        f"title: {title}",
        "type: wiki",
        "status: active",
        "canonical: true",
        "source_kind: generated",
        "temperature: warm",
        "---",
        "",
        f"# {title}",
        "",
        "## Summary",
        summary,
        "",
        "## Pages",
    ]
    lines.extend(links or ["- None"])
    return "\n".join(lines).strip() + "\n"


def _render_global_index_page(
    *,
    root_rel: str,
    family_links: tuple[str, ...],
    recent_pages: tuple[WikiPageEntry, ...],
    family_counts: dict[str, int],
) -> str:
    lines = [
        "---",
        "title: Wiki",
        "type: wiki",
        "status: active",
        "canonical: true",
        "source_kind: generated",
        "temperature: warm",
        f"updated: {date.today().isoformat()}",
        "---",
        "",
        "# Wiki",
        "",
        "## Summary",
        "Compiled wiki entry point with recent pages and family navigation.",
        "",
        "## Navigation",
        f"- [[{root_rel}/hot|Hot Cache]]",
        f"- [[{root_rel}/log|Activity Log]]",
    ]
    lines.extend(list(family_links) or ["- None"])
    lines.extend(["", "## Family Counts"])
    for family in _WIKI_FAMILIES:
        lines.append(f"- {family.title()}: {family_counts.get(family, 0)}")
    lines.extend(["", "## Recent Pages"])
    if recent_pages:
        for entry in recent_pages:
            lines.append(
                f"- [[{root_rel}/{entry.family}/{entry.path.stem}|{entry.title}]] ({entry.updated or 'unknown'})"
            )
    else:
        lines.append("- None")
    return "\n".join(lines).strip() + "\n"


def _render_hot_page(
    *,
    current_focus: str,
    recent_pages: tuple[WikiPageEntry, ...],
    session_lines: tuple[str, ...],
    claim_context: ClaimHotContext,
) -> str:
    latest_update = recent_pages[0].updated if recent_pages and recent_pages[0].updated else date.today().isoformat()
    lines = [
        "---",
        "title: Hot Cache",
        "type: wiki",
        "status: active",
        "canonical: true",
        "source_kind: generated",
        "temperature: warm",
        f"updated: {date.today().isoformat()}",
        "---",
        "",
        "# Recent Context",
        "",
        "## Summary",
        current_focus or "Generated recent-context cache for fast memory routing.",
        "",
        "## Last Updated",
        f"- {latest_update}: refreshed from compiled wiki and recent session activity.",
        "",
        "## Current Focus",
        f"- {current_focus}",
        "",
        "## Key Recent Facts",
    ]
    if claim_context.recent_facts:
        for fact in claim_context.recent_facts:
            lines.append(f"- {fact}")
    elif recent_pages:
        for entry in recent_pages[:3]:
            lines.append(f"- {entry.title}: {entry.summary or 'No summary.'}")
    else:
        lines.append("- None")
    lines.extend(["", "## Recent Changes"])
    if claim_context.recent_changes:
        lines.extend(f"- {change}" for change in claim_context.recent_changes)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Recent Pages",
        ]
    )
    if recent_pages:
        for entry in recent_pages:
            lines.append(f"- {entry.updated or 'unknown'}: {entry.title} [{entry.family}]")
    else:
        lines.append("- None")
    lines.extend(["", "## Active Threads"])
    lines.extend(list(session_lines) or ["- None"])
    return "\n".join(lines).strip() + "\n"


def _render_log_page(
    *,
    claim_context: ClaimHotContext,
    recent_pages: tuple[WikiPageEntry, ...],
    session_lines: tuple[str, ...],
) -> str:
    lines = [
        "---",
        "title: Activity Log",
        "type: wiki",
        "status: active",
        "canonical: true",
        "source_kind: generated",
        "temperature: warm",
        f"updated: {date.today().isoformat()}",
        "---",
        "",
        "# Activity Log",
        "",
        "## Summary",
        "Generated recent wiki and session activity log.",
        "",
        "## Recent Claim Changes",
    ]
    if claim_context.recent_changes:
        lines.extend(f"- {change}" for change in claim_context.recent_changes)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Recent Wiki Changes",
        ]
    )
    if recent_pages:
        for entry in recent_pages:
            lines.append(f"- {entry.updated or 'unknown'}: {entry.title} [{entry.family}]")
    else:
        lines.append("- None")
    lines.extend(["", "## Recent Session Activity"])
    lines.extend(list(session_lines) or ["- None"])
    return "\n".join(lines).strip() + "\n"


def _page_sort_key(path: Path) -> tuple[str, str]:
    frontmatter = _page_frontmatter(path)
    updated = frontmatter.get("updated")
    updated_text = updated[:10] if isinstance(updated, str) and _looks_like_date(updated[:10]) else ""
    return updated_text, _page_label(path).casefold()


def _page_label(path: Path) -> str:
    title = _page_frontmatter(path).get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return path.stem.replace("-", " ").title()


def _page_summary(path: Path) -> str:
    try:
        body = load_markdown_document(path.read_text(encoding="utf-8")).body
    except ValueError:
        body = path.read_text(encoding="utf-8")
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("## "):
            continue
        if line.startswith("- "):
            return line[2:].strip()[:180]
        return line[:180]
    return ""


def _page_updated(path: Path) -> str:
    updated = _page_frontmatter(path).get("updated")
    if isinstance(updated, str) and updated.strip():
        return updated[:10]
    return ""


def _render_claim_change_line(detail) -> str:
    statement = detail.statement.strip()
    if statement:
        return f"{detail.created_at[:10]}: {detail.event_type} {statement}"
    if detail.reason:
        return f"{detail.created_at[:10]}: {detail.event_type} ({detail.reason.strip()})"
    return ""


def _entity_id_for_wiki_entry(family: str, slug: str) -> str | None:
    mapping = {
        "people": "person",
        "projects": "project",
        "concepts": "concept",
        "decisions": "decision",
    }
    entity_family = mapping.get(family)
    if entity_family is None:
        return None
    return f"{entity_family}:{slug}"


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        value = item.strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(value)
    return deduped


def _page_frontmatter(path: Path) -> dict[str, object]:
    try:
        return load_markdown_document(path.read_text(encoding="utf-8")).frontmatter
    except ValueError:
        return {}


def _looks_like_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
