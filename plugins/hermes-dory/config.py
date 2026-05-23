from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
import os

import yaml


# ── type aliases ──────────────────────────────────────────────────────────

SearchMode = Literal[
    "hybrid", "lexical", "text", "keyword", "semantic", "recall", "bm25", "vector", "exact"
]
HttpSearchMode = Literal["hybrid", "recall", "bm25", "vector", "exact"]
SearchCorpus = Literal["durable", "sessions", "all"]
RerankMode = Literal["auto", "true", "false"]
MemoryMode = Literal["hybrid", "context", "tools"]
WakeProfile = str
ActiveMemoryProfile = str
ResearchKind = Literal["report", "briefing", "wiki-note", "proposal"]
ResearchCorpus = Literal["durable", "sessions", "all"]
SessionStatus = Literal["active", "interrupted", "done"]
ResearchPublishVisibility = Literal["internal", "public", "private"]

# ── constants ─────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "http://127.0.0.1:8766"
_DEFAULT_HERMES_HOME = Path.home() / ".hermes"
_PROVIDER_CONFIG_PATHS = ("dory.yaml", "dory.yml", "dory/config.yaml")
_MAIN_CONFIG_PATHS = ("config.yaml", "config.yml")
_DORY_CONFIG_KEYS = {
    "base_url",
    "token",
    "default_agent",
    "wake_budget_tokens",
    "wake_profile",
    "wake_recent_sessions",
    "wake_include_pinned_decisions",
    "active_memory_include_wake",
    "search_k",
    "search_mode",
    "memory_mode",
}


# ── general helpers (reused by config + tools) ────────────────────────────

def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


# ── config value health helpers ───────────────────────────────────────────

def _safe_int(value: str | int | None, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except ValueError:
        return default


def _safe_bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _safe_search_mode(value: str | None, *, default: SearchMode) -> SearchMode:
    if value in {"hybrid", "lexical", "text", "keyword", "semantic", "recall", "bm25", "vector", "exact"}:
        return value  # type: ignore[return-value]
    return default


def _safe_memory_mode(value: str | None, *, default: MemoryMode) -> MemoryMode:
    if value in {"hybrid", "context", "tools"}:
        return value  # type: ignore[return-value]
    return default


def _safe_wake_profile(value: str | None, *, default: WakeProfile) -> WakeProfile:
    normalized = _as_optional_string(value)
    return normalized or default


def _normalize_search_mode(mode: SearchMode) -> HttpSearchMode:
    if mode in {"lexical", "text", "keyword"}:
        return "bm25"
    if mode == "semantic":
        return "vector"
    return mode  # type: ignore[return-value]


# ── config-yaml helpers ───────────────────────────────────────────────────

def _nested_mapping(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _pick_config_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _pick_config_int(payload: dict[str, Any], key: str, *, fallback: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _safe_int(value, default=fallback)
    return fallback


def _pick_config_bool(payload: dict[str, Any], key: str, *, fallback: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _safe_bool(value, default=fallback)
    return fallback


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _looks_like_dory_config(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in _DORY_CONFIG_KEYS)


def _extract_dory_config(payload: dict[str, Any]) -> dict[str, Any]:
    if _looks_like_dory_config(payload):
        return payload
    direct = _nested_mapping(payload, "dory")
    if _looks_like_dory_config(direct):
        return direct
    provider = _nested_value(payload, "memory", "provider")
    if isinstance(provider, dict):
        nested = provider.get("dory")
        if isinstance(nested, dict) and _looks_like_dory_config(nested):
            return nested
        if _looks_like_dory_config(provider):
            return provider
    providers = _nested_mapping(payload, "memory", "providers", "dory")
    if _looks_like_dory_config(providers):
        return providers
    return {}


def _iter_hermes_config_candidates(
    *,
    path: Path | None,
    hermes_home: str | Path | None,
) -> tuple[Path, ...]:
    if path is not None:
        return (Path(path),)
    roots: list[Path] = []
    if hermes_home is not None:
        roots.append(Path(hermes_home))
    roots.append(_DEFAULT_HERMES_HOME)
    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_roots.append(resolved)
    candidates: list[Path] = []
    for root in unique_roots:
        for rel_path in _PROVIDER_CONFIG_PATHS:
            candidates.append(root / rel_path)
        for rel_path in _MAIN_CONFIG_PATHS:
            candidates.append(root / rel_path)
    return tuple(candidates)


# ── config dataclass ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class HermesDoryProviderConfig:
    base_url: str
    token: str | None = None
    default_agent: str = "hermes"
    wake_budget_tokens: int = 600
    wake_profile: WakeProfile = "coding"
    wake_recent_sessions: int = 5
    wake_include_pinned_decisions: bool = True
    active_memory_include_wake: bool = False
    search_k: int = 8
    search_mode: SearchMode = "hybrid"
    memory_mode: MemoryMode = "hybrid"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HermesDoryProviderConfig:
        source = dict(os.environ if env is None else env)
        base_url = source.get("DORY_HTTP_URL", _DEFAULT_BASE_URL).strip() or _DEFAULT_BASE_URL
        return cls(
            base_url=base_url,
            token=source.get("DORY_HTTP_TOKEN") or source.get("DORY_CLIENT_AUTH_TOKEN"),
            default_agent=source.get("DORY_HERMES_AGENT", "hermes").strip() or "hermes",
            wake_budget_tokens=_safe_int(source.get("DORY_HERMES_WAKE_BUDGET_TOKENS"), default=600),
            wake_profile=_safe_wake_profile(source.get("DORY_HERMES_WAKE_PROFILE"), default="coding"),
            wake_recent_sessions=_safe_int(source.get("DORY_HERMES_WAKE_RECENT_SESSIONS"), default=5),
            wake_include_pinned_decisions=_safe_bool(
                source.get("DORY_HERMES_WAKE_INCLUDE_PINNED_DECISIONS"),
                default=True,
            ),
            active_memory_include_wake=_safe_bool(
                source.get("DORY_HERMES_ACTIVE_MEMORY_INCLUDE_WAKE"),
                default=False,
            ),
            search_k=_safe_int(source.get("DORY_HERMES_SEARCH_K"), default=8),
            search_mode=_safe_search_mode(source.get("DORY_HERMES_SEARCH_MODE"), default="hybrid"),
            memory_mode=_safe_memory_mode(source.get("DORY_HERMES_MEMORY_MODE"), default="hybrid"),
        )

    @classmethod
    def from_hermes_config(
        cls,
        path: Path | None = None,
        *,
        hermes_home: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> HermesDoryProviderConfig:
        env_config = cls.from_env(env)
        for candidate in _iter_hermes_config_candidates(path=path, hermes_home=hermes_home):
            dory_section = _extract_dory_config(_load_yaml_mapping(candidate))
            if not dory_section:
                continue
            return cls(
                base_url=_pick_config_string(dory_section, "base_url") or env_config.base_url,
                token=_pick_config_string(dory_section, "token") or env_config.token,
                default_agent=_pick_config_string(dory_section, "default_agent") or env_config.default_agent,
                wake_budget_tokens=_pick_config_int(
                    dory_section,
                    "wake_budget_tokens",
                    fallback=env_config.wake_budget_tokens,
                ),
                wake_profile=_safe_wake_profile(
                    _pick_config_string(dory_section, "wake_profile"),
                    default=env_config.wake_profile,
                ),
                wake_recent_sessions=_pick_config_int(
                    dory_section,
                    "wake_recent_sessions",
                    fallback=env_config.wake_recent_sessions,
                ),
                wake_include_pinned_decisions=_pick_config_bool(
                    dory_section,
                    "wake_include_pinned_decisions",
                    fallback=env_config.wake_include_pinned_decisions,
                ),
                active_memory_include_wake=_pick_config_bool(
                    dory_section,
                    "active_memory_include_wake",
                    fallback=env_config.active_memory_include_wake,
                ),
                search_k=_pick_config_int(dory_section, "search_k", fallback=env_config.search_k),
                search_mode=_safe_search_mode(
                    _pick_config_string(dory_section, "search_mode"),
                    default=env_config.search_mode,
                ),
                memory_mode=_safe_memory_mode(
                    _pick_config_string(dory_section, "memory_mode"),
                    default=env_config.memory_mode,
                ),
            )
        return env_config
