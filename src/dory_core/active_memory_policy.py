from __future__ import annotations

import re

from dory_core.profiles import ProfileRegistry, RetrievalProfileConfig
from dory_core.types import ActiveMemoryReq

_TOPIC_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{1,}")
_TOPIC_STOPWORDS = {
    "about",
    "after",
    "agent",
    "agents",
    "and",
    "are",
    "can",
    "check",
    "code",
    "coding",
    "current",
    "debug",
    "did",
    "do",
    "does",
    "fix",
    "for",
    "from",
    "how",
    "into",
    "issue",
    "last",
    "make",
    "mcp",
    "memory",
    "need",
    "now",
    "on",
    "project",
    "repo",
    "search",
    "setup",
    "test",
    "the",
    "this",
    "today",
    "what",
    "when",
    "where",
    "with",
    "work",
    "working",
}

_PromptContext = str
_ActiveMemoryProfile = str


class SourcePolicy:
    """Source policy controlling what paths are allowed for memory retrieval."""

    profile: _ActiveMemoryProfile
    retrieval: RetrievalProfileConfig
    include_session_context: bool

    prompt_context: _PromptContext

    __slots__ = ("profile", "retrieval", "include_session_context", "prompt_context")

    def __init__(
        self,
        profile: _ActiveMemoryProfile,
        retrieval: RetrievalProfileConfig,
        include_session_context: bool,
        prompt_context: _PromptContext,
    ) -> None:
        self.profile = profile
        self.retrieval = retrieval
        self.include_session_context = include_session_context
        self.prompt_context = prompt_context

    def allows_result_path(self, path: str, *, corpus: str) -> bool:
        if corpus == "sessions":
            return self.include_session_context
        if self.prompt_context == "health" and _is_non_health_context_path(path):
            return False
        return self.retrieval.allows_path(path, corpus=corpus)

    def path_weight(self, path: str) -> float:
        if self.prompt_context == "health":
            if path.startswith("knowledge/health/"):
                return 1.5
            if path == "projects/dee-supplement-plan/state.md":
                return 1.4
            if path == "projects/cut-phase/state.md":
                return 0.8
            if path.startswith("core/") or path.startswith("people/"):
                return -2.0
        return self.retrieval.path_weight(path)

    def needs_prefilter_expansion(self, *, corpus: str) -> bool:
        if corpus == "sessions":
            return False
        return bool(self.retrieval.allow or self.retrieval.deny or not self.retrieval.include_durable_context)


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def prompt_context(prompt: str) -> str:
    lowered = prompt.casefold()
    if contains_any(
        lowered,
        (
            "privacy",
            "private",
            "sensitive",
            "boundary",
            "boundaries",
            "redact",
            "public-safe",
            "do not share",
        ),
    ):
        return "privacy"
    if contains_any(
        lowered,
        (
            "supplement",
            "supplements",
            "nutrition",
            "vitamin",
            "vitamins",
            "creatine",
            "protein",
            "lactoferrin",
            "carnitine",
            "magnesium",
            "zinc",
            "tongkat",
            "ashwagandha",
            "cut phase",
            "bulking",
            "health plan",
        ),
    ):
        return "health"
    if contains_any(
        lowered,
        (
            "who am i",
            "about me",
            "my profile",
            "personal",
            "preference",
            "preferences",
            "how should you talk to me",
        ),
    ):
        return "personal"
    if contains_any(
        lowered,
        (
            "writing",
            "voice",
            "draft",
            "copy",
            "post",
            "essay",
            "tone",
            "style",
            "blog",
        ),
    ):
        return "writing"
    if contains_any(
        lowered,
        (
            "code",
            "coding",
            "repo",
            "implementation",
            "bug",
            "test",
            "tests",
            "api",
            "integration",
            "integrations",
            "schema",
            "mcp",
            "module",
        ),
    ):
        return "coding"
    return "general"


def prompt_needs_session_context(prompt: str) -> bool:
    lowered = prompt.casefold()
    return contains_any(
        lowered,
        (
            "last worked",
            "worked on last",
            "what did i work",
            "recent session",
            "latest session",
            "previous session",
            "session context",
            "conversation",
            "yesterday",
            "today",
            "this morning",
            "last night",
        ),
    )


def resolve_active_memory_profile(req: ActiveMemoryReq) -> str:
    if req.profile != "auto":
        return req.profile
    return prompt_context(req.prompt)


def include_session_context(prompt: str, sessions_policy: str) -> bool:
    if sessions_policy == "always":
        return True
    if sessions_policy == "never":
        return False
    return prompt_needs_session_context(prompt)


def active_memory_path_weight(path: str) -> float:
    if path == "core/active.md":
        return 0.9
    if path in {"core/env.md", "core/defaults.md", "core/user.md", "core/soul.md"}:
        return 0.45
    if path.startswith("projects/") and path.endswith("/state.md"):
        return 0.65
    if path.startswith("decisions/canonical/"):
        return 0.5
    if path.startswith("people/"):
        return 0.25
    if path.startswith("knowledge/"):
        return 0.15
    if path.startswith("wiki/"):
        return -1.0
    if path.startswith("logs/"):
        return -0.8
    if path.startswith(("inbox/", "archive/")):
        return -0.5
    return 0.0


def is_active_memory_candidate(result: object, *, corpus: str, active_profile: str = "general") -> bool:
    path = str(getattr(result, "path", "") or "")
    if not path or path.endswith(".tombstone.md"):
        return False
    if corpus == "durable" and path.startswith("logs/sessions/"):
        return False
    if corpus == "durable" and path.startswith("wiki/"):
        return False
    if corpus == "durable" and path.startswith(("inbox/", "archive/")):
        return False
    frontmatter = _result_frontmatter(result)
    status = str(frontmatter.get("status", "")).strip().lower()
    if status in {"retired", "stale", "superseded", "quarantined", "quarantine"}:
        return False
    if corpus == "durable" and status == "raw":
        return False
    if corpus == "durable" and _is_historical_or_generated_source(path, frontmatter):
        return False
    if corpus == "durable" and _is_private_or_sensitive_source(frontmatter, active_profile=active_profile):
        return False
    confidence = str(getattr(result, "confidence", "") or "").strip().lower()
    if corpus == "durable" and confidence == "low":
        return False
    return True


def _result_frontmatter(result: object) -> dict[str, object]:
    frontmatter = getattr(result, "frontmatter", {})
    return frontmatter if isinstance(frontmatter, dict) else {}


def filter_active_memory_results(
    results: list[object],
    *,
    corpus: str,
    source_policy: "SourcePolicy",
) -> list[object]:
    return [
        result
        for result in results
        if is_active_memory_candidate(result, corpus=corpus, active_profile=source_policy.profile)
        and source_policy.allows_result_path(str(getattr(result, "path", "") or ""), corpus=corpus)
    ]


def source_policy_for_request(req: ActiveMemoryReq, *, profile_registry: ProfileRegistry) -> "SourcePolicy":
    profile = resolve_active_memory_profile(req)
    retrieval = profile_registry.retrieval_profile(profile)
    context = prompt_context(req.prompt)
    session_included = include_session_context(req.prompt, retrieval.sessions)
    return SourcePolicy(
        profile=profile,
        retrieval=retrieval,
        include_session_context=session_included,
        prompt_context=context,
    )


def _is_historical_or_generated_source(path: str, frontmatter: dict[str, object]) -> bool:
    temperature = str(frontmatter.get("temperature", "") or "").strip().lower()
    if temperature == "cold":
        return True

    source_kind = str(frontmatter.get("source_kind", "") or "").strip().lower()
    if source_kind in {"raw", "session", "imported", "legacy"}:
        return True
    if source_kind == "generated":
        return True
    if path.startswith(("digests/", "logs/daily/", "logs/weekly/", "sources/semantic/")):
        return True
    return False


def _is_private_or_sensitive_source(frontmatter: dict[str, object], *, active_profile: str) -> bool:
    visibility = str(frontmatter.get("visibility", "") or "").strip().lower()
    if visibility == "private" and active_profile in {"coding", "writing", "general", "assistant", "admin"}:
        return True

    sensitivity = str(frontmatter.get("sensitivity", "") or "").strip().lower()
    if sensitivity in {"credentials", "contact", "financial", "legal"}:
        return True
    if sensitivity == "health" and active_profile not in {"assistant", "personal"}:
        return True
    if sensitivity == "personal" and active_profile in {"coding", "privacy", "admin"}:
        return True
    return False


def _is_non_health_context_path(path: str) -> bool:
    if path.startswith("knowledge/health/"):
        return False
    if path in {
        "projects/dee-supplement-plan/state.md",
        "projects/cut-phase/state.md",
    }:
        return False
    return True


def topic_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token
        for token in (_normalize_topic_token(match.group(0)) for match in _TOPIC_TOKEN_RE.finditer(text))
        if token and token not in _TOPIC_STOPWORDS and len(token) >= 3
    )


def text_matches_topic(text: str, prompt_tokens: frozenset[str]) -> bool:
    if not text:
        return False
    text_tokens = topic_tokens(text)
    if not text_tokens:
        return False
    return bool(prompt_tokens & text_tokens)


def _normalize_topic_token(token: str) -> str:
    return token.strip("_-").casefold()


# Re-export for backward compatibility
_PromptContext = prompt_context
