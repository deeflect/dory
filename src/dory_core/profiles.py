from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import yaml

BuiltinWakeProfile = Literal["default", "casual", "coding", "writing", "privacy"]
BuiltinActiveMemoryProfile = Literal["general", "coding", "writing", "privacy", "personal"]
SessionsPolicy = Literal["intent_only", "always", "never"]

_DEFAULT_WAKE_SECTION_ORDERS: dict[str, tuple[str, ...]] = {
    "default": ("core/user.md", "core/soul.md", "core/env.md", "core/active.md", "core/identity.md", "core/defaults.md"),
    "casual": ("core/user.md", "core/soul.md", "core/identity.md", "core/defaults.md", "core/active.md", "core/env.md"),
    "coding": ("core/active.md", "core/env.md", "core/defaults.md"),
    "writing": ("core/soul.md", "knowledge/personal/writing-voice.md", "core/defaults.md", "core/active.md"),
    "privacy": ("privacy_boundaries", "core/defaults.md", "core/soul.md"),
}

_DEFAULT_WAKE_SECTION_BUDGETS: dict[str, dict[str, int]] = {
    "default": {"project": 360},
    "casual": {"project": 320},
    "coding": {
        "active": 480,
        "env": 340,
        "defaults": 260,
        "project": 360,
        "user": 220,
        "soul": 220,
        "identity": 180,
    },
    "writing": {
        "soul": 520,
        "writing_voice": 420,
        "defaults": 180,
        "active": 180,
        "project": 260,
    },
    "privacy": {
        "privacy_boundaries": 420,
        "defaults": 260,
        "soul": 180,
        "project": 220,
    },
}

_DEFAULT_PATH_WEIGHTS: dict[str, float] = {
    "core/active.md": 0.9,
    "core/env.md": 0.45,
    "core/defaults.md": 0.45,
    "core/user.md": 0.45,
    "core/soul.md": 0.45,
    "projects/*/state.md": 0.65,
    "decisions/canonical/**": 0.5,
    "people/**": 0.25,
    "knowledge/**": 0.15,
    "wiki/**": -1.0,
    "logs/**": -0.8,
    "inbox/**": -0.5,
    "archive/**": -0.5,
}


@dataclass(frozen=True, slots=True)
class WakeProfileConfig:
    sections: tuple[str, ...]
    section_budgets: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalProfileConfig:
    wake_profile: str
    include_pinned_decisions: bool = True
    include_durable_context: bool = True
    sessions: SessionsPolicy = "intent_only"
    use_helper_context: bool = True
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    boosts: dict[str, float] = field(default_factory=dict)

    def allows_path(self, path: str, *, corpus: str) -> bool:
        if corpus == "sessions":
            return self.sessions != "never"
        if not self.include_durable_context:
            return False
        if self.deny and any(_path_matches(pattern, path) for pattern in self.deny):
            return False
        if self.allow and not any(_path_matches(pattern, path) for pattern in self.allow):
            return False
        return True

    def path_weight(self, path: str) -> float:
        for pattern, weight in self.boosts.items():
            if _path_matches(pattern, path):
                return float(weight)
        for pattern, weight in _DEFAULT_PATH_WEIGHTS.items():
            if _path_matches(pattern, path):
                return weight
        return 0.0


@dataclass(frozen=True, slots=True)
class MemoryProfileConfig:
    name: str
    wake: WakeProfileConfig
    retrieval: RetrievalProfileConfig


class ProfileRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        builtin_profiles = _builtin_profiles()
        self._sources: dict[str, str] = dict.fromkeys(builtin_profiles, "builtin")
        self._profiles = builtin_profiles
        custom_profiles = _load_profiles_file(self.root / "profiles.yaml")
        self._profiles.update(custom_profiles)
        self._sources.update(dict.fromkeys(custom_profiles, "custom"))

    def wake_profile(self, name: str) -> WakeProfileConfig:
        profile = self._profiles.get(name) or self._profiles["default"]
        return profile.wake

    def retrieval_profile(self, name: str) -> RetrievalProfileConfig:
        profile = self._profiles.get(name)
        if profile is not None:
            return profile.retrieval
        return self._profiles["general"].retrieval

    def profile_names(self) -> list[str]:
        return sorted(self._profiles)

    def describe_profiles(self) -> list[dict[str, Any]]:
        return [_describe_profile(name, profile, source=self._sources.get(name, "builtin")) for name, profile in sorted(self._profiles.items())]


def _builtin_profiles() -> dict[str, MemoryProfileConfig]:
    profiles: dict[str, MemoryProfileConfig] = {}
    for name, sections in _DEFAULT_WAKE_SECTION_ORDERS.items():
        profiles[name] = MemoryProfileConfig(
            name=name,
            wake=WakeProfileConfig(sections=sections, section_budgets=dict(_DEFAULT_WAKE_SECTION_BUDGETS.get(name, {}))),
            retrieval=_builtin_retrieval_profile(name),
        )
    profiles["general"] = MemoryProfileConfig(
        name="general",
        wake=profiles["default"].wake,
        retrieval=_builtin_retrieval_profile("general"),
    )
    profiles["personal"] = MemoryProfileConfig(
        name="personal",
        wake=profiles["default"].wake,
        retrieval=_builtin_retrieval_profile("personal"),
    )
    return profiles


def _builtin_retrieval_profile(name: str) -> RetrievalProfileConfig:
    if name == "privacy":
        return RetrievalProfileConfig(
            wake_profile="privacy",
            include_pinned_decisions=False,
            include_durable_context=False,
            sessions="never",
            use_helper_context=False,
        )
    if name == "coding":
        return RetrievalProfileConfig(
            wake_profile="coding",
            include_pinned_decisions=False,
            include_durable_context=True,
            sessions="intent_only",
            use_helper_context=True,
            deny=("core/user.md", "core/soul.md", "core/identity.md", "people/**", "knowledge/personal/**"),
        )
    if name == "writing":
        return RetrievalProfileConfig(
            wake_profile="writing",
            include_pinned_decisions=True,
            include_durable_context=True,
            sessions="intent_only",
            use_helper_context=True,
            deny=("core/user.md", "core/identity.md", "people/**"),
        )
    if name == "personal":
        return RetrievalProfileConfig(
            wake_profile="default",
            include_pinned_decisions=True,
            include_durable_context=True,
            sessions="intent_only",
            use_helper_context=False,
        )
    return RetrievalProfileConfig(
        wake_profile="default",
        include_pinned_decisions=True,
        include_durable_context=True,
        sessions="intent_only",
        use_helper_context=True,
    )


def _load_profiles_file(path: Path) -> dict[str, MemoryProfileConfig]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_profiles = payload.get("profiles", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_profiles, dict):
        return {}
    profiles: dict[str, MemoryProfileConfig] = {}
    for raw_name, raw_profile in raw_profiles.items():
        if not isinstance(raw_name, str) or not isinstance(raw_profile, dict):
            continue
        name = raw_name.strip()
        if not name:
            continue
        wake_payload = raw_profile.get("wake", {}) if isinstance(raw_profile.get("wake", {}), dict) else {}
        retrieval_payload = raw_profile.get("retrieval", {}) if isinstance(raw_profile.get("retrieval", {}), dict) else {}
        builtin = _builtin_profiles().get(name)
        default_wake = builtin.wake if builtin is not None else WakeProfileConfig(sections=())
        default_retrieval = builtin.retrieval if builtin is not None else _builtin_retrieval_profile("general")
        sections = _string_tuple(wake_payload.get("sections")) or default_wake.sections
        section_budgets = dict(default_wake.section_budgets)
        raw_budgets = wake_payload.get("budgets")
        if isinstance(raw_budgets, dict):
            section_budgets.update(_float_dict(raw_budgets, value_type=int))
            section_budgets.update(_float_dict({str(key).split("/")[-1].removesuffix(".md"): value for key, value in raw_budgets.items()}, value_type=int))
        retrieval = RetrievalProfileConfig(
            wake_profile=str(retrieval_payload.get("wake_profile") or name),
            include_pinned_decisions=_bool_value(
                retrieval_payload.get("include_pinned_decisions"), default_retrieval.include_pinned_decisions
            ),
            include_durable_context=_bool_value(
                retrieval_payload.get("include_durable_context"), default_retrieval.include_durable_context
            ),
            sessions=_sessions_policy(retrieval_payload.get("sessions"), default_retrieval.sessions),
            use_helper_context=_bool_value(retrieval_payload.get("use_helper_context"), default_retrieval.use_helper_context),
            allow=_string_tuple(retrieval_payload.get("allow")),
            deny=(*default_retrieval.deny, *_string_tuple(retrieval_payload.get("deny"))),
            boosts={**default_retrieval.boosts, **_float_dict(retrieval_payload.get("boosts"), value_type=float)},
        )
        profiles[name] = MemoryProfileConfig(
            name=name,
            wake=WakeProfileConfig(sections=sections, section_budgets=section_budgets),
            retrieval=retrieval,
        )
    return profiles


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _describe_profile(name: str, profile: MemoryProfileConfig, *, source: str) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "wake": {
            "sections": list(profile.wake.sections),
            "budgets": dict(profile.wake.section_budgets),
        },
        "retrieval": {
            "wake_profile": profile.retrieval.wake_profile,
            "include_pinned_decisions": profile.retrieval.include_pinned_decisions,
            "include_durable_context": profile.retrieval.include_durable_context,
            "sessions": profile.retrieval.sessions,
            "use_helper_context": profile.retrieval.use_helper_context,
            "allow": list(profile.retrieval.allow),
            "deny": list(profile.retrieval.deny),
            "boosts": dict(profile.retrieval.boosts),
        },
    }


def _float_dict(value: object, *, value_type: type = float) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, Any] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            continue
        try:
            parsed[key] = value_type(raw_value)
        except (TypeError, ValueError):
            continue
    return parsed


def _bool_value(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    return fallback


def _sessions_policy(value: object, fallback: SessionsPolicy) -> SessionsPolicy:
    if value in {"intent_only", "always", "never"}:
        return value  # type: ignore[return-value]
    return fallback


def _path_matches(pattern: str, path: str) -> bool:
    if pattern == path:
        return True
    return fnmatchcase(path, pattern)
