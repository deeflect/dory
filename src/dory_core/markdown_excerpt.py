from __future__ import annotations

from pathlib import Path

_COMPOSER_SNIPPET_CHARS = 360


def truncate_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def extract_markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    if marker not in text:
        return ""
    section = text.split(marker, 1)[1]
    if "\n## " in section:
        section = section.split("\n## ", 1)[0]
    return section


def section_list_excerpt(text: str, heading: str) -> str:
    section = extract_markdown_section(text, heading)
    if not section:
        return ""
    items: list[str] = []
    for raw_line in section.splitlines():
        line = clean_markdown_content_line(raw_line)
        if not line:
            continue
        items.append(line)
        if len(items) >= 2:
            break
    return truncate_text(" ".join(items), _COMPOSER_SNIPPET_CHARS)


def first_content_excerpt(text: str) -> str:
    items: list[str] = []
    for raw_line in text.splitlines():
        line = clean_markdown_content_line(raw_line)
        if not line:
            continue
        items.append(line)
        if len(items) >= 2:
            break
    return truncate_text(" ".join(items), _COMPOSER_SNIPPET_CHARS)


def clean_markdown_content_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line or line == "---":
        return ""
    if line.startswith("#"):
        return ""
    if line.startswith(">"):
        if "shared hot-context" in line.casefold():
            return ""
        return line.lstrip("> ").strip()
    if line.startswith("- "):
        line = line[2:].strip()
    if "shared hot-context loaded into every agent" in line.casefold():
        return ""
    return line


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    return parts[1] if len(parts) == 2 else text


def canonical_file_excerpt(root: Path, rel_path: str) -> str:
    if rel_path.startswith(("logs/", "inbox/", "archive/", "wiki/")):
        return ""
    try:
        path = (root / rel_path).resolve()
        root_resolved = root.resolve()
        path.relative_to(root_resolved)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = strip_frontmatter(text)
    for heading in ("Summary", "Current Focus", "Current State", "Open Work", "Topology", "Defaults"):
        excerpt = section_list_excerpt(body, heading)
        if excerpt:
            return excerpt
    return first_content_excerpt(body)


def safe_evidence_text(text: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        lowered = line.lower()
        if lowered.startswith(("system:", "developer:", "assistant:", "tool:")):
            continue
        if lowered.startswith("user:"):
            line = line[5:].strip()
        normalized_lines.append(line)
    return " ".join(" ".join(normalized_lines).split())


def result_evidence_text(result: object, *, root: Path | None) -> str:
    path = str(getattr(result, "path", "") or "")
    snippet = safe_evidence_text(str(getattr(result, "snippet", "") or ""))
    if root is not None and path:
        excerpt = canonical_file_excerpt(root, path)
        if excerpt:
            if snippet and snippet.casefold() not in excerpt.casefold():
                return truncate_text(f"{snippet} {excerpt}", _COMPOSER_SNIPPET_CHARS)
            return excerpt
    return snippet
