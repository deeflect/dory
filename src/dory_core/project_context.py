from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.slug import slugify_path_segment


def resolve_project_handle(*, project: str | None, cwd: str | None, root: Path | None) -> str | None:
    explicit = (project or "").strip()
    if explicit:
        path_handle = infer_project_handle_from_path(explicit, cwd=cwd)
        if path_handle and resolve_project_path(root, path_handle) is not None:
            return path_handle
        if resolve_project_path(root, explicit) is not None:
            return explicit
        return explicit

    inferred = infer_project_handle_from_cwd(cwd)
    if inferred and resolve_project_path(root, inferred) is not None:
        return inferred
    return None


def resolve_project_path(root: Path | None, project: str) -> Path | None:
    if root is None:
        return None
    projects_root = root / "projects"
    if not projects_root.exists():
        return None

    normalized = project.strip()
    if normalized.startswith("project:"):
        normalized = normalized.split(":", 1)[1]

    wanted_values = _lookup_candidates(normalized)
    for wanted in wanted_values:
        direct = projects_root / wanted / "state.md"
        if direct.exists():
            return direct

    entries = _project_entries(projects_root)
    for entry in entries:
        if any(wanted in entry.lookup_values for wanted in wanted_values):
            return entry.path

    return _best_fuzzy_project_match(entries, wanted_values)


def infer_project_handle_from_path(value: str, *, cwd: str | None = None) -> str | None:
    path = Path(value).expanduser()
    if not _looks_like_path(value):
        return None
    candidates: list[str] = []

    paths = [path]
    if cwd and not path.is_absolute():
        paths.insert(0, Path(cwd).expanduser() / path)
    for candidate_path in paths:
        for ancestor in (candidate_path, *candidate_path.parents):
            candidates.extend(_project_names_from_local_manifests(ancestor))
        candidates.extend(part for part in reversed(candidate_path.parts) if part and part not in {"/", ".", ".."})

    for candidate in candidates:
        normalized = slugify_path_segment(candidate)
        if normalized:
            return normalized
    return None


def infer_project_handle_from_cwd(cwd: str | None) -> str | None:
    if cwd is None or not cwd.strip():
        return None
    path = Path(cwd).expanduser()
    candidates: list[str] = []
    for ancestor in (path, *path.parents):
        candidates.extend(_project_names_from_local_manifests(ancestor))
    candidates.extend(part for part in reversed(path.parts) if part and part not in {"/", "."})
    for candidate in candidates:
        normalized = slugify_path_segment(candidate)
        if normalized:
            return normalized
    return None


def _project_names_from_local_manifests(path: Path) -> list[str]:
    candidates: list[str] = []
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            payload = {}
        project = payload.get("project") if isinstance(payload, dict) else None
        name = project.get("name") if isinstance(project, dict) else None
        if isinstance(name, str) and name.strip():
            candidates.append(name.strip())

    package_json = path / "package.json"
    if package_json.exists():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        name = payload.get("name") if isinstance(payload, dict) else None
        if isinstance(name, str) and name.strip():
            candidates.append(name.strip().split("/", 1)[-1])

    return candidates


@dataclass(frozen=True, slots=True)
class _ProjectEntry:
    path: Path
    lookup_values: frozenset[str]


def _project_entries(projects_root: Path) -> list[_ProjectEntry]:
    entries: list[_ProjectEntry] = []
    for path in sorted(projects_root.glob("*/state.md")):
        try:
            document = load_markdown_document(path.read_text(encoding="utf-8"))
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
        entries.append(
            _ProjectEntry(
                path=path,
                lookup_values=frozenset(slugify_path_segment(value) for value in values if value.strip()),
            )
        )
    return entries


def _lookup_candidates(value: str) -> list[str]:
    raw_candidates = [value]
    if _looks_like_path(value):
        raw_path = Path(value).expanduser()
        raw_candidates.extend(part for part in reversed(raw_path.parts) if part and part not in {"/", ".", ".."})
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        normalized = slugify_path_segment(candidate.removeprefix("project:").strip())
        if normalized and normalized not in seen:
            candidates.append(normalized)
            seen.add(normalized)
    return candidates


def _best_fuzzy_project_match(entries: list[_ProjectEntry], wanted_values: list[str]) -> Path | None:
    if not wanted_values:
        return None
    scored: list[tuple[int, str, Path]] = []
    for entry in entries:
        score = max(_fuzzy_score(wanted, value) for wanted in wanted_values for value in entry.lookup_values)
        if score >= 60:
            scored.append((score, entry.path.as_posix(), entry.path))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, _best_name, best_path = scored[0]
    if len(scored) > 1 and best_score == scored[1][0]:
        return None
    return best_path


def _fuzzy_score(wanted: str, candidate: str) -> int:
    if not wanted or not candidate:
        return 0
    if wanted == candidate:
        return 100
    if len(wanted) >= 4 and (candidate.startswith(wanted) or wanted.startswith(candidate)):
        return 85
    if len(wanted) >= 4 and (wanted in candidate or candidate in wanted):
        return 75
    wanted_tokens = set(wanted.split("-"))
    candidate_tokens = set(candidate.split("-"))
    if not wanted_tokens or not candidate_tokens:
        return 0
    overlap = wanted_tokens & candidate_tokens
    if not overlap:
        return 0
    if overlap == wanted_tokens or overlap == candidate_tokens:
        return 70
    return int(50 * (len(overlap) / len(wanted_tokens | candidate_tokens)))


def _looks_like_path(value: str) -> bool:
    return value.startswith(("~", ".", "/")) or "/" in value or "\\" in value
