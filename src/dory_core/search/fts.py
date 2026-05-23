from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Sequence

from dory_core.search.policies import _TEMPORAL_QUERY_TOKENS
from dory_core.search.types import QueryProfile

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_FTS_SEGMENT_RE = re.compile(r"[A-Za-z0-9]+(?:[./@_-][A-Za-z0-9]+)+")
_FTS_QUOTED_SEGMENT_RE = re.compile(r"`([^`]+)`")

_STOPWORD_TOKENS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "did",
        "do",
        "for",
        "how",
        "i",
        "in",
        "is",
        "it",
        "meant",
        "of",
        "on",
        "other",
        "or",
        "pick",
        "run",
        "stop",
        "the",
        "to",
        "using",
        "we",
        "what",
        "when",
        "why",
    }
)


def _build_fts_query(query: str) -> str:
    """Turn a free-form user query into an FTS5-safe expression.

    FTS5 treats punctuation like ``.``, ``-``, ``/`` as syntax when it shows up
    bare.  Terms such as ``GPT-5.4`` or ``foo.bar`` blow up the parser.  We
    preserve meaningful punctuated identifiers as phrases, drop low-signal
    stopwords, and OR the remaining clauses together so BM25 scores the actual
    nouns and tool names instead of generic question glue.
    """
    clauses: list[str] = []
    seen: set[str] = set()

    for segment in _FTS_QUOTED_SEGMENT_RE.findall(query):
        parts = [part.lower() for part in _FTS_TOKEN_RE.findall(segment)]
        if len(parts) < 2:
            continue
        phrase = " ".join(parts)
        if phrase and phrase not in seen:
            clauses.append(f'"{phrase}"')
            seen.add(phrase)
        for part in parts:
            if part in _STOPWORD_TOKENS or len(part) < 2 or part in seen:
                continue
            clauses.append(f'"{part}"')
            seen.add(part)

    for segment in _FTS_SEGMENT_RE.findall(query):
        parts = [part.lower() for part in _FTS_TOKEN_RE.findall(segment)]
        if len(parts) < 2:
            continue
        phrase = " ".join(parts)
        if phrase and phrase not in seen:
            clauses.append(f'"{phrase}"')
            seen.add(phrase)
        for part in parts:
            if part in _STOPWORD_TOKENS or len(part) < 2 or part in seen:
                continue
            clauses.append(f'"{part}"')
            seen.add(part)

    for token in _FTS_TOKEN_RE.findall(query):
        lowered = token.lower()
        if lowered in _STOPWORD_TOKENS or len(lowered) < 2 or lowered in seen:
            continue
        clauses.append(f'"{lowered}"')
        seen.add(lowered)

    if not clauses:
        return ""
    return " OR ".join(clauses)


def _dedupe_preserve_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _normalize_match_token(token: str) -> str:
    lowered = token.lower()
    if len(lowered) > 5 and lowered.endswith("ing"):
        return lowered[:-3]
    if len(lowered) > 4 and lowered.endswith("ied"):
        return f"{lowered[:-3]}y"
    if len(lowered) > 4 and lowered.endswith("ed"):
        return lowered[:-2]
    if len(lowered) > 4 and lowered.endswith("es"):
        return lowered[:-2]
    if len(lowered) > 3 and lowered.endswith("s"):
        return lowered[:-1]
    return lowered


def _extract_normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize_match_token(token) for token in _FTS_TOKEN_RE.findall(text.lower()) if len(token) >= 2)


def _normalize_text_for_matching(text: str) -> str:
    return " ".join(_extract_normalized_tokens(text))


def _has_close_identifier_match(token: str, identifier_tokens: set[str]) -> bool:
    if len(token) < 5 or not identifier_tokens:
        return False
    for candidate in identifier_tokens:
        if abs(len(token) - len(candidate)) > 2:
            continue
        if token[0] != candidate[0]:
            continue
        if SequenceMatcher(None, token, candidate).ratio() >= 0.82:
            return True
    return False


def _extract_match_phrases(query: str) -> tuple[str, ...]:
    phrases: list[str] = []
    for segment in _FTS_QUOTED_SEGMENT_RE.findall(query):
        normalized = _normalize_text_for_matching(segment)
        if normalized:
            phrases.append(normalized)
    for segment in _FTS_SEGMENT_RE.findall(query):
        normalized = _normalize_text_for_matching(segment)
        if normalized:
            phrases.append(normalized)
    return tuple(phrases)


def _build_query_profile(query: str) -> QueryProfile:
    raw_tokens = [token.lower() for token in _FTS_TOKEN_RE.findall(query)]
    normalized_tokens = _dedupe_preserve_order(
        [_normalize_match_token(token) for token in raw_tokens if token not in _STOPWORD_TOKENS and len(token) >= 3]
    )
    phrases = _dedupe_preserve_order(_extract_match_phrases(query))
    has_identifier_hint = bool(_FTS_SEGMENT_RE.search(query)) or any(char in query for char in "@/._-")
    has_temporal_hint = bool(set(raw_tokens) & _TEMPORAL_QUERY_TOKENS)
    return QueryProfile(
        tokens=normalized_tokens,
        phrases=phrases,
        has_identifier_hint=has_identifier_hint,
        has_temporal_hint=has_temporal_hint,
    )


def _focused_snippet(body: str, *, query: str, limit: int) -> str:
    query_profile = _build_query_profile(query)
    if not query_profile.tokens:
        return ""
    query_tokens = set(query_profile.tokens)
    lines = [line.strip() for line in body.splitlines()]
    content_lines = [line for line in lines if line and not line.startswith("#")]
    if not content_lines:
        return ""
    for index, line in enumerate(content_lines):
        line_tokens = set(_extract_normalized_tokens(line))
        if not (query_tokens & line_tokens):
            continue
        start = max(0, index - 1)
        end = min(len(content_lines), index + 3)
        return " ".join(" ".join(content_lines[start:end]).split())[:limit]
    return ""
