from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    frontmatter: dict[str, object]
    body: str
    raw: str


def load_markdown_document(text: str) -> MarkdownDocument:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("markdown document is missing frontmatter")

    closing_index = _find_closing_fence(lines)
    if closing_index is None:
        raise ValueError("markdown document has an unterminated frontmatter block")

    header_lines = lines[1:closing_index]
    body = "".join(lines[closing_index + 1 :])
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]

    return MarkdownDocument(
        frontmatter=_parse_frontmatter_lines(header_lines),
        body=body,
        raw=text,
    )


def _find_closing_fence(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    raw_header = "".join(lines)
    try:
        payload = yaml.safe_load(raw_header) or {}
    except yaml.YAMLError:
        payload = _parse_legacy_frontmatter_lines(lines)
    if not isinstance(payload, dict):
        raise ValueError("frontmatter payload must be a mapping")
    return {str(key): _normalize_loaded_value(value) for key, value in payload.items()}


def _parse_legacy_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    frontmatter: dict[str, object] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if ":" not in stripped:
            raise ValueError(f"invalid frontmatter line: {stripped}")

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value:
            frontmatter[key] = _parse_scalar(value)
            index += 1
            continue

        list_items: list[object] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                index += 1
                continue
            if candidate[:1].isspace() and candidate_stripped.startswith("- "):
                list_items.append(_parse_scalar(candidate_stripped[2:].strip()))
                index += 1
                continue
            break
        frontmatter[key] = list_items

    return frontmatter


def _normalize_loaded_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_loaded_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_loaded_value(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat) and not isinstance(value, str):
        return isoformat()
    return value


def _parse_scalar(value: str) -> object:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def dump_markdown_document(frontmatter: dict[str, Any], body: str) -> str:
    rendered_frontmatter = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    return f"---\n{rendered_frontmatter}\n---\n\n{body.rstrip()}\n"


def merge_frontmatter(
    existing: dict[str, Any],
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing)
    if incoming is None:
        return merged

    for key, value in incoming.items():
        if key == "tags":
            merged[key] = _merge_tags(existing.get(key), value)
        else:
            merged[key] = value
    return merged


def _merge_tags(existing: Any, incoming: Any) -> list[str]:
    merged: list[str] = []
    for raw in (existing, incoming):
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            rendered = str(value)
            if rendered not in merged:
                merged.append(rendered)
    return merged
