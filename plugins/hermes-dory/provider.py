from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

import httpx
import yaml

try:
    from agent.memory_provider import MemoryProvider
except ImportError:

    class MemoryProvider:
        pass


SearchMode = Literal["hybrid", "lexical", "text", "keyword", "semantic", "recall", "bm25", "vector", "exact"]
HttpSearchMode = Literal["hybrid", "recall", "bm25", "vector", "exact"]
SearchCorpus = Literal["durable", "sessions", "all"]
MemoryMode = Literal["hybrid", "context", "tools"]
WakeProfile = Literal["default", "casual", "coding", "writing", "privacy"]
ActiveMemoryProfile = Literal["auto", "general", "coding", "writing", "privacy", "personal"]
ResearchKind = Literal["report", "briefing", "wiki-note", "proposal"]
ResearchCorpus = Literal["durable", "sessions", "all"]
SessionStatus = Literal["active", "interrupted", "done"]

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


@dataclass(frozen=True, slots=True)
class SessionTurn:
    role: Literal["user", "assistant"]
    content: str


class _SupportsRequest(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class DoryMemoryProvider(MemoryProvider):
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        client: _SupportsRequest | None = None,
        *,
        default_agent: str = "hermes",
        wake_budget_tokens: int = 600,
        wake_profile: WakeProfile = "coding",
        wake_recent_sessions: int = 5,
        wake_include_pinned_decisions: bool = True,
        active_memory_include_wake: bool = False,
        search_k: int = 8,
        search_mode: SearchMode = "hybrid",
        memory_mode: MemoryMode = "hybrid",
    ) -> None:
        self.client = client
        self._owned_client: httpx.Client | None = None
        self.base_url = (base_url or "").strip()
        self.token = token
        self.default_agent = default_agent
        self.wake_budget_tokens = wake_budget_tokens
        self.wake_profile = wake_profile
        self.wake_recent_sessions = wake_recent_sessions
        self.wake_include_pinned_decisions = wake_include_pinned_decisions
        self.active_memory_include_wake = active_memory_include_wake
        self.search_k = search_k
        self.search_mode = search_mode
        self.memory_mode = memory_mode
        self._explicit_config = any(
            [
                base_url is not None,
                token is not None,
                default_agent != "hermes",
                wake_budget_tokens != 600,
                wake_profile != "coding",
                wake_recent_sessions != 5,
                wake_include_pinned_decisions is not True,
                active_memory_include_wake is not False,
                search_k != 8,
                search_mode != "hybrid",
                memory_mode != "hybrid",
            ]
        )
        self._session_id = ""
        self._runtime_agent = default_agent
        self._platform = "cli"
        self._agent_context = "primary"
        self._session_device = "hermes-cli"
        self._hermes_home = _DEFAULT_HERMES_HOME
        self._writes_enabled = True
        self._prefetch_cache_query = ""
        self._prefetch_cache_session_id = ""
        self._prefetch_cache = ""
        self._session_turns: list[SessionTurn] = []
        self._refresh_owned_client()

    @property
    def name(self) -> str:
        return "dory"

    @classmethod
    def from_config(
        cls,
        config: HermesDoryProviderConfig,
        *,
        client: _SupportsRequest | None = None,
    ) -> DoryMemoryProvider:
        return cls(
            base_url=config.base_url,
            token=config.token,
            client=client,
            default_agent=config.default_agent,
            wake_budget_tokens=config.wake_budget_tokens,
            wake_profile=config.wake_profile,
            wake_recent_sessions=config.wake_recent_sessions,
            wake_include_pinned_decisions=config.wake_include_pinned_decisions,
            active_memory_include_wake=config.active_memory_include_wake,
            search_k=config.search_k,
            search_mode=config.search_mode,
            memory_mode=config.memory_mode,
        )

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        client: _SupportsRequest | None = None,
    ) -> DoryMemoryProvider:
        return cls.from_config(HermesDoryProviderConfig.from_env(env), client=client)

    @classmethod
    def from_hermes_config(
        cls,
        path: Path | None = None,
        *,
        hermes_home: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        client: _SupportsRequest | None = None,
    ) -> DoryMemoryProvider:
        return cls.from_config(
            HermesDoryProviderConfig.from_hermes_config(path, hermes_home=hermes_home, env=env),
            client=client,
        )

    def is_available(self) -> bool:
        if self.base_url:
            return True
        config = HermesDoryProviderConfig.from_hermes_config(hermes_home=self._hermes_home)
        return bool(config.base_url.strip())

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        hermes_home = kwargs.get("hermes_home")
        if isinstance(hermes_home, str) and hermes_home.strip():
            self._hermes_home = Path(hermes_home)
        elif isinstance(hermes_home, Path):
            self._hermes_home = hermes_home
        self._platform = str(kwargs.get("platform", "cli"))
        self._agent_context = str(kwargs.get("agent_context", "primary"))
        agent_identity = str(kwargs.get("agent_identity") or "").strip()
        self._runtime_agent = agent_identity or self.default_agent
        self._session_id = session_id
        self._session_device = f"hermes-{self._platform}"
        self._writes_enabled = self._agent_context == "primary"
        self._session_turns = []
        self._prefetch_cache_query = ""
        self._prefetch_cache_session_id = ""
        self._prefetch_cache = ""
        if not self._explicit_config:
            self._apply_config(
                HermesDoryProviderConfig.from_hermes_config(hermes_home=self._hermes_home),
            )
            if agent_identity:
                self._runtime_agent = agent_identity

    def system_prompt_block(self) -> str:
        if self.memory_mode == "context":
            return "External memory provider: Dory. Relevant durable memory is prefetched automatically."
        return (
            "External memory provider: Dory. Use dory_search and dory_get before claiming durable facts. "
            "Use dory_memory_write for explicit durable writes."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self.memory_mode == "tools":
            return ""
        if (
            query == self._prefetch_cache_query
            and session_id == self._prefetch_cache_session_id
            and self._prefetch_cache
        ):
            cached = self._prefetch_cache
            self._prefetch_cache_query = ""
            self._prefetch_cache_session_id = ""
            self._prefetch_cache = ""
            return cached
        try:
            return self.build_memory_section(query)
        except RuntimeError:
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self.memory_mode == "tools":
            return
        try:
            self._prefetch_cache = self.build_memory_section(query)
            self._prefetch_cache_query = query
            self._prefetch_cache_session_id = session_id
        except RuntimeError:
            self._prefetch_cache = ""
            self._prefetch_cache_query = ""
            self._prefetch_cache_session_id = ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._writes_enabled:
            return
        if session_id:
            self._session_id = session_id
        user_text = user_content.strip()
        assistant_text = assistant_content.strip()
        if not user_text and not assistant_text:
            return
        if user_text:
            self._session_turns.append(SessionTurn(role="user", content=user_text))
        if assistant_text:
            self._session_turns.append(SessionTurn(role="assistant", content=assistant_text))
        try:
            self._session_ingest(status="active", turns=self._session_turns)
        except RuntimeError:
            return

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        if self.memory_mode == "context":
            return []
        return _build_tool_schemas()

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        try:
            if tool_name == "dory_wake":
                return json.dumps(
                    self.wake(
                        agent=_as_optional_string(args.get("agent")),
                        budget_tokens=_as_optional_int(args.get("budget_tokens")),
                        profile=_as_optional_wake_profile(args.get("profile")),
                        include_recent_sessions=_as_optional_int(args.get("include_recent_sessions")),
                        include_pinned_decisions=_as_optional_bool(args.get("include_pinned_decisions")),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_active_memory":
                prompt = _require_string(args, "prompt")
                return json.dumps(
                    self.active_memory(
                        prompt,
                        agent=_as_optional_string(args.get("agent")),
                        budget_tokens=_as_optional_int(args.get("budget_tokens")),
                        cwd=_as_optional_string(args.get("cwd")),
                        timeout_ms=_as_optional_int(args.get("timeout_ms")),
                        profile=_as_optional_active_memory_profile(args.get("profile")),
                        include_wake=_as_optional_bool(args.get("include_wake")),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_research":
                question = _require_string(args, "question")
                return json.dumps(
                    self.research(
                        question,
                        kind=_as_optional_research_kind(args.get("kind")) or "report",
                        corpus=_as_optional_research_corpus(args.get("corpus")) or "all",
                        limit=_as_optional_int(args.get("limit")),
                        save=_as_optional_bool(args.get("save"), default=True),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_search":
                query = _require_string(args, "query")
                return json.dumps(
                    self.search(
                        query,
                        k=_as_optional_int(args.get("k")),
                        mode=_as_optional_search_mode(args.get("mode")),
                        corpus=_as_optional_search_corpus(args.get("corpus")),
                        scope=_as_optional_mapping(args.get("scope")),
                        include_content=_as_optional_bool(args.get("include_content")),
                        min_score=_as_optional_float(args.get("min_score")),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_get":
                path = _require_string(args, "path")
                return json.dumps(
                    self.get(
                        path,
                        from_line=_as_optional_int(args.get("from"), default=1),
                        lines=_as_optional_int(args.get("lines")),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_memory_write":
                return json.dumps(
                    self.memory_write(
                        action=_require_string(args, "action"),
                        kind=_require_string(args, "kind"),
                        subject=_require_string(args, "subject"),
                        content=_require_string(args, "content"),
                        scope=_as_optional_string(args.get("scope")),
                        confidence=_as_optional_string(args.get("confidence")),
                        reason=_as_optional_string(args.get("reason")),
                        source=_as_optional_string(args.get("source")),
                        soft=_as_optional_bool(args.get("soft"), default=False),
                        dry_run=_as_optional_bool(args.get("dry_run"), default=False),
                        force_inbox=_as_optional_bool(args.get("force_inbox"), default=False),
                        allow_canonical=_as_optional_bool(args.get("allow_canonical"), default=False),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_write":
                return json.dumps(
                    self.write(
                        kind=_require_string(args, "kind"),
                        target=_require_string(args, "target"),
                        content=_as_optional_string(args.get("content")) or "",
                        soft=_as_optional_bool(args.get("soft"), default=False),
                        dry_run=_as_optional_bool(args.get("dry_run"), default=False),
                        frontmatter=_as_optional_mapping(args.get("frontmatter")),
                        agent=_as_optional_string(args.get("agent")),
                        session_id=_as_optional_string(args.get("session_id")),
                        expected_hash=_as_optional_string(args.get("expected_hash")),
                        reason=_as_optional_string(args.get("reason")),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_purge":
                return json.dumps(
                    self.purge(
                        target=_require_string(args, "target"),
                        expected_hash=_as_optional_string(args.get("expected_hash")),
                        reason=_as_optional_string(args.get("reason")),
                        dry_run=_as_optional_bool(args.get("dry_run"), default=True),
                        allow_canonical=_as_optional_bool(args.get("allow_canonical"), default=False),
                        include_related_tombstone=_as_optional_bool(
                            args.get("include_related_tombstone"),
                            default=False,
                        ),
                    ),
                    sort_keys=True,
                )
            if tool_name == "dory_link":
                payload = dict(args)
                return json.dumps(self.link(payload), sort_keys=True)
            if tool_name == "dory_status":
                return json.dumps(self.status(), sort_keys=True)
        except (RuntimeError, ValueError, TypeError) as err:
            return json.dumps({"ok": False, "error": str(err)}, sort_keys=True)
        return json.dumps({"ok": False, "error": f"unsupported tool: {tool_name}"}, sort_keys=True)

    def shutdown(self) -> None:
        self.close()

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._writes_enabled:
            return
        turns = _session_turns_from_messages(messages)
        if turns:
            self._session_turns = turns
        if not self._session_turns:
            return
        try:
            self._session_ingest(status="done", turns=self._session_turns)
        except RuntimeError:
            return

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if not self._writes_enabled:
            return
        mapped_action = _map_builtin_memory_action(action)
        if mapped_action is None or not content.strip():
            return
        try:
            if target == "user":
                self.memory_write(
                    action=mapped_action,
                    kind="preference",
                    subject="user",
                    content=content,
                    scope="core",
                    source="hermes-builtin-user",
                )
                return
            self.write(
                kind="append",
                target="inbox/hermes-memory-mirror.md",
                content=_format_builtin_memory_mirror(action=action, target=target, content=content),
                frontmatter={
                    "title": "Hermes built-in memory mirror",
                    "type": "capture",
                },
            )
        except RuntimeError:
            return

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "base_url",
                "description": "Dory HTTP base URL.",
                "required": True,
                "default": _DEFAULT_BASE_URL,
            },
            {
                "key": "default_agent",
                "description": "Default agent identity sent to Dory.",
                "default": "hermes",
            },
            {
                "key": "memory_mode",
                "description": "How Hermes should use Dory: hybrid, context-only, or tools-only.",
                "default": "hybrid",
                "choices": ["hybrid", "context", "tools"],
            },
            {
                "key": "search_mode",
                "description": "Default Dory search mode.",
                "default": "hybrid",
                "choices": ["hybrid", "recall", "bm25", "vector", "exact"],
            },
            {
                "key": "wake_budget_tokens",
                "description": "Wake/active-memory token budget.",
                "default": 600,
            },
            {
                "key": "wake_profile",
                "description": "Default Dory wake profile.",
                "default": "coding",
                "choices": ["default", "casual", "coding", "writing", "privacy"],
            },
            {
                "key": "active_memory_include_wake",
                "description": "Whether active_memory should include the wake block when Hermes already prefetched wake.",
                "default": False,
            },
            {
                "key": "search_k",
                "description": "Default number of search results to request.",
                "default": 8,
            },
            {
                "key": "token",
                "description": "Optional bearer token for the Dory HTTP server.",
                "secret": True,
                "env_var": "DORY_HTTP_TOKEN",
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        target = Path(hermes_home) / "dory.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base_url": _as_optional_string(values.get("base_url")) or _DEFAULT_BASE_URL,
            "default_agent": _as_optional_string(values.get("default_agent")) or "hermes",
            "memory_mode": _as_optional_string(values.get("memory_mode")) or "hybrid",
            "search_mode": _as_optional_string(values.get("search_mode")) or "hybrid",
            "wake_budget_tokens": _as_optional_int(values.get("wake_budget_tokens"), default=600),
            "wake_profile": _as_optional_string(values.get("wake_profile")) or "coding",
            "active_memory_include_wake": _as_optional_bool(
                values.get("active_memory_include_wake"),
                default=False,
            ),
            "search_k": _as_optional_int(values.get("search_k"), default=8),
        }
        target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def wake(
        self,
        *,
        agent: str | None = None,
        budget_tokens: int | None = None,
        profile: WakeProfile | None = None,
        include_recent_sessions: int | None = None,
        include_pinned_decisions: bool | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/wake",
            json={
                "agent": self._resolve_agent(agent),
                "budget_tokens": budget_tokens if budget_tokens is not None else self.wake_budget_tokens,
                "profile": profile if profile is not None else self.wake_profile,
                "include_recent_sessions": (
                    include_recent_sessions if include_recent_sessions is not None else self.wake_recent_sessions
                ),
                "include_pinned_decisions": (
                    include_pinned_decisions
                    if include_pinned_decisions is not None
                    else self.wake_include_pinned_decisions
                ),
            },
        )

    def search(
        self,
        query: str,
        *,
        k: int | None = None,
        mode: SearchMode | None = None,
        corpus: SearchCorpus | None = None,
        scope: dict[str, Any] | None = None,
        include_content: bool | None = None,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        normalized_mode = _normalize_search_mode(mode or self.search_mode)
        payload: dict[str, Any] = {
            "query": query,
            "k": k if k is not None else self.search_k,
            "mode": normalized_mode,
        }
        if corpus is not None:
            payload["corpus"] = corpus
        if scope is not None:
            payload["scope"] = scope
        if include_content is not None:
            payload["include_content"] = include_content
        if min_score is not None:
            payload["min_score"] = min_score
        return self._request("POST", "/v1/search", json=payload)

    def get(self, path: str, *, from_line: int = 1, lines: int | None = None) -> dict[str, Any]:
        params = {"path": path, "from": from_line}
        if lines is not None:
            params["lines"] = lines
        return self._request("GET", "/v1/get", params=params)

    def write(
        self,
        *,
        kind: str,
        target: str,
        content: str = "",
        soft: bool = False,
        dry_run: bool = False,
        frontmatter: dict[str, Any] | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        expected_hash: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": kind,
            "target": target,
            "content": content,
            "soft": soft,
            "dry_run": dry_run,
        }
        if frontmatter is not None:
            payload["frontmatter"] = frontmatter
        if agent is not None:
            payload["agent"] = agent
        if session_id is not None:
            payload["session_id"] = session_id
        if expected_hash is not None:
            payload["expected_hash"] = expected_hash
        if reason is not None:
            payload["reason"] = reason
        return self._request("POST", "/v1/write", json=payload)

    def memory_write(
        self,
        *,
        action: str,
        kind: str,
        subject: str,
        content: str,
        scope: str | None = None,
        confidence: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        soft: bool = False,
        dry_run: bool = False,
        force_inbox: bool = False,
        allow_canonical: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": action,
            "kind": kind,
            "subject": subject,
            "content": content,
            "soft": soft,
            "dry_run": dry_run,
            "force_inbox": force_inbox,
            "allow_canonical": allow_canonical,
        }
        if scope is not None:
            payload["scope"] = scope
        if confidence is not None:
            payload["confidence"] = confidence
        if reason is not None:
            payload["reason"] = reason
        if source is not None:
            payload["source"] = source
        return self._request("POST", "/v1/memory-write", json=payload)

    def purge(
        self,
        *,
        target: str,
        expected_hash: str | None = None,
        reason: str | None = None,
        dry_run: bool = True,
        allow_canonical: bool = False,
        include_related_tombstone: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": target,
            "dry_run": dry_run,
            "allow_canonical": allow_canonical,
            "include_related_tombstone": include_related_tombstone,
        }
        if expected_hash is not None:
            payload["expected_hash"] = expected_hash
        if reason is not None:
            payload["reason"] = reason
        return self._request("POST", "/v1/purge", json=payload)

    def research(
        self,
        question: str,
        *,
        kind: ResearchKind = "report",
        corpus: ResearchCorpus = "all",
        limit: int | None = None,
        save: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question": question,
            "kind": kind,
            "corpus": corpus,
        }
        if limit is not None:
            payload["limit"] = limit
        if save is not None:
            payload["save"] = save
        return self._request("POST", "/v1/research", json=payload)

    def link(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/link", json=payload)

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def active_memory(
        self,
        prompt: str,
        *,
        agent: str | None = None,
        budget_tokens: int | None = None,
        cwd: str | None = None,
        timeout_ms: int | None = None,
        profile: ActiveMemoryProfile | None = None,
        include_wake: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": prompt,
            "agent": self._resolve_agent(agent),
            "budget_tokens": budget_tokens if budget_tokens is not None else self.wake_budget_tokens,
            "include_wake": include_wake if include_wake is not None else self.active_memory_include_wake,
        }
        if profile is not None:
            payload["profile"] = profile
        if cwd is not None:
            payload["cwd"] = cwd
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return self._request("POST", "/v1/active-memory", json=payload)

    def prefetch_bundle(
        self,
        prompt: str,
        *,
        agent: str | None = None,
        budget_tokens: int | None = None,
        k: int | None = None,
        mode: SearchMode | None = None,
        cwd: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        wake_payload = self.wake(agent=agent, budget_tokens=budget_tokens)
        search_payload = self.search(prompt, k=k, mode=mode)
        active_memory_payload = self.active_memory(
            prompt,
            agent=agent,
            budget_tokens=budget_tokens,
            cwd=cwd,
            timeout_ms=timeout_ms,
            include_wake=False,
        )
        return {
            "wake": wake_payload,
            "search": search_payload,
            "active_memory": active_memory_payload,
        }

    def build_memory_section(
        self,
        prompt: str,
        *,
        agent: str | None = None,
        budget_tokens: int | None = None,
        k: int | None = None,
        mode: SearchMode | None = None,
        cwd: str | None = None,
        timeout_ms: int | None = None,
    ) -> str:
        prefetched = self.prefetch_bundle(
            prompt,
            agent=agent,
            budget_tokens=budget_tokens,
            k=k,
            mode=mode,
            cwd=cwd,
            timeout_ms=timeout_ms,
        )
        active_memory = prefetched.get("active_memory")
        block = ""
        if isinstance(active_memory, dict):
            block = str(active_memory.get("block", "")).strip()
        if not block:
            block = str(prefetched["wake"].get("block", "")).strip()
        results = prefetched["search"].get("results", [])
        lines = ["# Dory Memory", ""]
        if block:
            lines.append(block)
            lines.append("")
        if isinstance(results, list) and results:
            lines.append("## Retrieved Evidence")
            for result in results[:5]:
                if not isinstance(result, dict):
                    continue
                path = str(result.get("path", "")).strip()
                snippet = str(result.get("snippet", "")).strip()
                if not path:
                    continue
                lines.append(f"- {path}")
                if snippet:
                    lines.append(f"  {snippet}")
        return "\n".join(lines).strip()

    def store_memory(
        self,
        *,
        content: str,
        subject: str | None = None,
        action: str = "write",
        kind: str = "fact",
        scope: str | None = None,
        confidence: str | None = None,
        reason: str | None = None,
        source: str | None = None,
        soft: bool = False,
        dry_run: bool = False,
        force_inbox: bool = False,
        allow_canonical: bool = False,
        target: str | None = None,
        write_kind: str = "append",
        frontmatter: dict[str, Any] | None = None,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        if subject is not None:
            return self.memory_write(
                action=action,
                kind=kind,
                subject=subject,
                content=content,
                scope=scope,
                confidence=confidence,
                reason=reason,
                source=source,
                soft=soft,
                dry_run=dry_run,
                force_inbox=force_inbox,
                allow_canonical=allow_canonical,
            )
        if target is None:
            raise ValueError("store_memory requires either subject or target")
        return self.write(
            kind=write_kind,
            target=target,
            content=content,
            soft=soft,
            dry_run=dry_run,
            frontmatter=frontmatter,
            expected_hash=expected_hash,
            reason=reason,
        )

    def sync_memories(self, writes: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        for write in writes:
            results.append(
                self.store_memory(
                    content=str(write["content"]),
                    subject=str(write["subject"]) if "subject" in write else None,
                    action=str(write.get("action", "write")),
                    kind=str(write.get("kind", "fact")),
                    scope=str(write["scope"]) if "scope" in write else None,
                    confidence=str(write["confidence"]) if "confidence" in write else None,
                    reason=str(write["reason"]) if "reason" in write else None,
                    source=str(write["source"]) if "source" in write else None,
                    soft=bool(write.get("soft", False)),
                    dry_run=bool(write.get("dry_run", False)),
                    force_inbox=bool(write.get("force_inbox", False)),
                    allow_canonical=bool(write.get("allow_canonical", False)),
                    target=str(write["target"]) if "target" in write else None,
                    write_kind=str(write.get("write_kind", write.get("kind", "append"))),
                    frontmatter=write.get("frontmatter"),
                    expected_hash=str(write["expected_hash"]) if "expected_hash" in write else None,
                )
            )
        return tuple(results)

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def _apply_config(self, config: HermesDoryProviderConfig) -> None:
        self.base_url = config.base_url.strip()
        self.token = config.token
        self.default_agent = config.default_agent
        self.wake_budget_tokens = config.wake_budget_tokens
        self.wake_profile = config.wake_profile
        self.wake_recent_sessions = config.wake_recent_sessions
        self.wake_include_pinned_decisions = config.wake_include_pinned_decisions
        self.active_memory_include_wake = config.active_memory_include_wake
        self.search_k = config.search_k
        self.search_mode = config.search_mode
        self.memory_mode = config.memory_mode
        self._refresh_owned_client()

    def _refresh_owned_client(self) -> None:
        if self.client is not None:
            return
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None
        if self.base_url:
            self._owned_client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        active_client = self.client or self._owned_client
        if active_client is None:
            raise RuntimeError("Dory provider is not initialized with a base URL or HTTP client")
        response = active_client.request(method, path, json=json, params=params, headers=headers)
        return self._parse_response(response)

    def _session_ingest(self, *, status: SessionStatus, turns: list[SessionTurn]) -> dict[str, Any]:
        if not turns:
            raise RuntimeError("no session turns available for ingest")
        return self._request(
            "POST",
            "/v1/session-ingest",
            json={
                "path": self._session_log_path(),
                "content": _render_session_turns(turns),
                "agent": self._runtime_agent,
                "device": self._session_device,
                "session_id": self._session_id or "hermes",
                "status": status,
                "captured_from": "hermes-memory-provider",
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )

    def _session_log_path(self) -> str:
        date_prefix = datetime.now(timezone.utc).date().isoformat()
        session_slug = _slugify(self._session_id or "session")
        agent_slug = _slugify(self._runtime_agent or self.default_agent or "hermes")
        return f"logs/sessions/hermes/{agent_slug}/{date_prefix}-{session_slug}.md"

    def _resolve_agent(self, agent: str | None) -> str:
        if agent is not None and agent.strip():
            return agent.strip()
        if self._runtime_agent.strip():
            return self._runtime_agent.strip()
        return self.default_agent

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"dory request failed: {response.status_code} {response.text}")
        return response.json()


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


def _build_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "dory_wake",
            "description": "Build the frozen wake-up block. Use profile='coding' for agent work, 'writing' for voice/content, or 'privacy' for boundary questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_tokens": {"type": "integer"},
                    "agent": {"type": "string"},
                    "profile": {
                        "type": "string",
                        "enum": ["default", "casual", "coding", "writing", "privacy"],
                    },
                    "include_recent_sessions": {"type": "integer"},
                    "include_pinned_decisions": {"type": "boolean"},
                },
            },
        },
        {
            "name": "dory_active_memory",
            "description": "Run the bounded active-memory pre-reply pass. Limits: budget_tokens <= 1200, timeout_ms <= 5000. Set include_wake=false if wake was already called.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent": {"type": "string"},
                    "cwd": {"type": "string"},
                    "profile": {
                        "type": "string",
                        "enum": ["auto", "general", "coding", "writing", "privacy", "personal"],
                    },
                    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 5000},
                    "budget_tokens": {"type": "integer", "minimum": 100, "maximum": 1200},
                    "include_wake": {"type": "boolean"},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "dory_research",
            "description": "Run Dory research mode and optionally save a durable artifact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "kind": {"type": "string", "enum": ["report", "briefing", "wiki-note", "proposal"]},
                    "corpus": {"type": "string", "enum": ["durable", "sessions", "all"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "save": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        {
            "name": "dory_search",
            "description": "Search the Dory memory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "hybrid",
                            "recall",
                            "bm25",
                            "text",
                            "keyword",
                            "lexical",
                            "vector",
                            "semantic",
                            "exact",
                        ],
                    },
                    "corpus": {"type": "string", "enum": ["durable", "sessions", "all"]},
                    "scope": {
                        "type": "object",
                        "properties": {
                            "path_glob": {"type": "string"},
                            "type": {"type": "array", "items": {"type": "string"}},
                            "status": {"type": "array", "items": {"type": "string"}},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "since": {"type": "string"},
                            "until": {"type": "string"},
                        },
                    },
                    "include_content": {"type": "boolean"},
                    "min_score": {"type": "number"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "dory_get",
            "description": "Fetch a file or line slice by path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "from": {"type": "integer"},
                    "lines": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "dory_memory_write",
            "description": "Write semantic memory through Dory using write, replace, or forget intent. Semantic subjects can route into canonical docs; set dry_run=true to preview, allow_canonical=true to commit a canonical write, or force_inbox=true for tentative/scratch captures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "replace", "forget"]},
                    "kind": {"type": "string", "enum": ["fact", "preference", "state", "decision", "note"]},
                    "subject": {"type": "string"},
                    "content": {"type": "string"},
                    "scope": {"type": "string", "enum": ["person", "project", "concept", "decision", "core"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                    "soft": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "force_inbox": {"type": "boolean"},
                    "allow_canonical": {"type": "boolean"},
                },
                "required": ["action", "kind", "subject", "content"],
            },
        },
        {
            "name": "dory_write",
            "description": "Exact-path markdown write. Use when you know the target path; replace/forget require expected_hash from dory_get. Set dry_run=true to validate and preview without writing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["append", "create", "replace", "forget"]},
                    "target": {"type": "string"},
                    "content": {"type": "string"},
                    "soft": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "frontmatter": {"type": "object"},
                    "agent": {"type": "string"},
                    "session_id": {"type": "string"},
                    "expected_hash": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "target"],
            },
        },
        {
            "name": "dory_purge",
            "description": "Hard-delete an exact markdown path from the corpus and index. Defaults to dry_run=true; live purge requires reason and matching expected_hash. Only scratch/generated roots are allowed unless allow_canonical=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "expected_hash": {"type": "string"},
                    "reason": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                    "allow_canonical": {"type": "boolean", "default": False},
                    "include_related_tombstone": {"type": "boolean", "default": False},
                },
                "required": ["target"],
            },
        },
        {
            "name": "dory_link",
            "description": "Inspect wikilink edges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["neighbors", "backlinks", "lint"]},
                    "path": {"type": "string"},
                    "direction": {"type": "string", "enum": ["out", "in", "both"]},
                    "depth": {"type": "integer"},
                },
                "required": ["op"],
            },
        },
        {
            "name": "dory_status",
            "description": "Get Dory index and corpus status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    ]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


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


def _looks_like_dory_config(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in _DORY_CONFIG_KEYS)


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
        return value
    return default


def _safe_memory_mode(value: str | None, *, default: MemoryMode) -> MemoryMode:
    if value in {"hybrid", "context", "tools"}:
        return value
    return default


def _safe_wake_profile(value: str | None, *, default: WakeProfile) -> WakeProfile:
    if value in {"default", "casual", "coding", "writing", "privacy"}:
        return value
    return default


def _normalize_search_mode(mode: SearchMode) -> HttpSearchMode:
    if mode in {"lexical", "text", "keyword"}:
        return "bm25"
    if mode == "semantic":
        return "vector"
    return mode


def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _as_optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return default


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _as_optional_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _safe_bool(str(value), default=default if default is not None else False)


def _as_optional_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raise TypeError("value must be an object")


def _as_optional_search_mode(value: Any) -> SearchMode | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"hybrid", "lexical", "text", "keyword", "semantic", "recall", "bm25", "vector", "exact"}:
        return string_value
    return None


def _as_optional_wake_profile(value: Any) -> WakeProfile | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"default", "casual", "coding", "writing", "privacy"}:
        return string_value
    return None


def _as_optional_active_memory_profile(value: Any) -> ActiveMemoryProfile | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"auto", "general", "coding", "writing", "privacy", "personal"}:
        return string_value
    return None


def _as_optional_research_kind(value: Any) -> ResearchKind | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"report", "briefing", "wiki-note", "proposal"}:
        return string_value
    return None


def _as_optional_research_corpus(value: Any) -> ResearchCorpus | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"durable", "sessions", "all"}:
        return string_value
    return None


def _as_optional_search_corpus(value: Any) -> SearchCorpus | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"durable", "sessions", "all"}:
        return string_value
    return None


def _require_string(args: dict[str, Any], key: str) -> str:
    value = _as_optional_string(args.get(key))
    if value is None:
        raise ValueError(f"missing required argument: {key}")
    return value


def _map_builtin_memory_action(action: str) -> Literal["write", "replace", "forget"] | None:
    if action == "add":
        return "write"
    if action == "replace":
        return "replace"
    if action == "remove":
        return "forget"
    return None


def _format_builtin_memory_mirror(*, action: str, target: str, content: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"[{timestamp}] action={action} target={target}\n{content.strip()}\n"


def _slugify(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    compact = "-".join(part for part in cleaned.split("-") if part)
    return compact or "session"


def _render_session_turns(turns: list[SessionTurn]) -> str:
    lines: list[str] = []
    for turn in turns:
        content = turn.content.strip()
        if not content:
            continue
        lines.append("## User" if turn.role == "user" else "## Assistant")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def _session_turns_from_messages(messages: list[dict[str, Any]]) -> list[SessionTurn]:
    turns: list[SessionTurn] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        rendered = _render_message_content(message.get("content"))
        if not rendered:
            continue
        turns.append(SessionTurn(role=role, content=rendered))
    return turns


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    chunks.append(text)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    chunks.append(text_value.strip())
                    continue
            text_value = item.get("content") or item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value.strip())
        return "\n\n".join(chunks).strip()
    if isinstance(content, dict):
        text_value = content.get("text") or content.get("content")
        if isinstance(text_value, str):
            return text_value.strip()
    return str(content).strip()
