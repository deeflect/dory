from __future__ import annotations

import re
import unicodedata

_INVALID_CHARS = re.compile(r"[^a-z0-9_/-]+")
_WHITESPACE = re.compile(r"\s+")
_HYPHENS = re.compile(r"-{2,}")


def slugify_path_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    hyphenated = _WHITESPACE.sub("-", ascii_only)
    cleaned = _INVALID_CHARS.sub("", hyphenated)
    collapsed = _HYPHENS.sub("-", cleaned)
    return collapsed.strip("-")
