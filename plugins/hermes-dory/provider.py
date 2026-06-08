from __future__ import annotations

import json
import sys as _sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Any, Mapping

import httpx
import yaml

# Ensure sibling modules are importable when this file is loaded directly
# (e.g. by tests using spec_from_file_location).
_THIS_DIR = _Path(__file__).resolve().parent
if str(_THIS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_THIS_DIR))

# ruff: noqa: E402 — local imports after sys.path insert for standalone loading
from config import (
    ActiveMemoryProfile,
    HermesDoryProviderConfig,
    MemoryMode,
    RerankMode,
    ResearchCorpus,
    ResearchKind,
    ResearchPublishVisibility,
    SearchCorpus,
    SearchMode,
    SessionStatus,
    WakeProfile,
    _DEFAULT_BASE_URL,
    _DEFAULT_HERMES_HOME,
    _normalize_search_mode,
    _safe_search_mode,
)
from client import DoryProviderError, _SupportsRequest
from tools import (
    SessionTurn,
    _as_optional_active_memory_profile,
    _as_optional_bool,
    _as_optional_float,
    _as_optional_int,
    _as_optional_mapping,
    _as_optional_rerank_mode,
    _as_optional_research_corpus,
    _as_optional_research_kind,
    _as_optional_research_publish_visibility,
    _as_optional_search_corpus,
    _as_optional_search_mode,
    _as_optional_string,
    _as_optional_string_list,
    _as_optional_wake_profile,
    _dedupe_strings,
    _format_builtin_memory_mirror,
    _map_builtin_memory_action,
    _render_research_knowledge_note,
    _render_session_turns,
    _require_string,
    _search_result_paths,
    _session_turns_from_messages,
    _slugify,
    _string_list,
    _tool_error_payload,
)
from schemas import _build_canonical_hermes_tool_schemas, _build_tool_schemas

# Re-export public names expected from this module and its test consumers.
HermesDoryProviderConfig = HermesDoryProviderConfig  # noqa: PLW0127
DoryProviderError = DoryProviderError  # noqa: PLW0127
_safe_search_mode = _safe_search_mode  # noqa: PLW0127
_normalize_search_mode = _normalize_search_mode  # noqa: PLW0127
_build_canonical_hermes_tool_schemas = _build_canonical_hermes_tool_schemas  # noqa: PLW0127


try:
    from agent.memory_provider import MemoryProvider
except ImportError:

    class MemoryProvider:  # type: ignore[no-redef]
        pass


@dataclass(frozen=True, slots=True)
class PrefetchPlan:
    profile: ActiveMemoryProfile
    include_search: bool = True


@dataclass(frozen=True, slots=True)
class PrefetchTrace:
    profile: ActiveMemoryProfile
    include_search: bool
    search_skipped: bool
    wake_sources: list[str]
    active_memory_sources: list[str]
    search_result_paths: list[str]
    injected_paths: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "include_search": self.include_search,
            "search_skipped": self.search_skipped,
            "wake_sources": self.wake_sources,
            "active_memory_sources": self.active_memory_sources,
            "search_result_paths": self.search_result_paths,
            "injected_paths": self.injected_paths,
        }


class DoryMemoryProvider(MemoryProvider):
    """Hermes memory provider backed by the Dory HTTP daemon."""

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
        inject_retrieved_evidence: bool = False,
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
        self.inject_retrieved_evidence = inject_retrieved_evidence
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
                inject_retrieved_evidence is not False,
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
            inject_retrieved_evidence=config.inject_retrieved_evidence,
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
        path: _Path | None = None,
        *,
        hermes_home: str | _Path | None = None,
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
            self._hermes_home = _Path(hermes_home)
        elif isinstance(hermes_home, _Path):
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
            handler = self._tool_handlers().get(tool_name)
            if handler is None:
                return json.dumps({"ok": False, "error": f"unsupported tool: {tool_name}"}, sort_keys=True)
            return json.dumps(handler(args), sort_keys=True)
        except DoryProviderError as err:
            return json.dumps(_tool_error_payload(err), sort_keys=True)
        except (RuntimeError, ValueError, TypeError) as err:
            return json.dumps({"ok": False, "error": str(err)}, sort_keys=True)

    def _tool_handlers(self):
        return {
            "dory_wake": self._handle_wake_tool,
            "dory_active_memory": self._handle_active_memory_tool,
            "dory_research": self._handle_research_tool,
            "dory_publish_research": self._handle_publish_research_tool,
            "dory_search": self._handle_search_tool,
            "dory_digest": self._handle_digest_tool,
            "dory_get": self._handle_get_tool,
            "dory_memory_write": self._handle_memory_write_tool,
            "dory_memory_propose": self._handle_memory_propose_tool,
            "dory_memory_proposals": self._handle_memory_proposals_tool,
            "dory_memory_proposal_get": self._handle_memory_proposal_get_tool,
            "dory_memory_proposal_apply": self._handle_memory_proposal_apply_tool,
            "dory_memory_proposal_reject": self._handle_memory_proposal_reject_tool,
            "dory_write": self._handle_write_tool,
            "dory_purge": self._handle_purge_tool,
            "dory_link": self._handle_link_tool,
            "dory_status": self._handle_status_tool,
        }

    # ── tool handler methods ──────────────────────────────────────────────

    def _handle_wake_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.wake(
            agent=_as_optional_string(args.get("agent")),
            budget_tokens=_as_optional_int(args.get("budget_tokens")),
            profile=_as_optional_wake_profile(args.get("profile")),
            project=_as_optional_string(args.get("project")),
            include_recent_sessions=_as_optional_int(args.get("include_recent_sessions")),
            include_pinned_decisions=_as_optional_bool(args.get("include_pinned_decisions")),
        )

    def _handle_active_memory_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.active_memory(
            _require_string(args, "prompt"),
            agent=_as_optional_string(args.get("agent")),
            budget_tokens=_as_optional_int(args.get("budget_tokens")),
            cwd=_as_optional_string(args.get("cwd")),
            project=_as_optional_string(args.get("project")),
            scope=_as_optional_mapping(args.get("scope")),
            timeout_ms=_as_optional_int(args.get("timeout_ms")),
            profile=_as_optional_active_memory_profile(args.get("profile")),
            include_wake=_as_optional_bool(args.get("include_wake")),
            rerank=_as_optional_rerank_mode(args.get("rerank")),
        )

    def _handle_research_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.research(
            _require_string(args, "question"),
            kind=_as_optional_research_kind(args.get("kind")) or "report",
            corpus=_as_optional_research_corpus(args.get("corpus")) or "all",
            limit=_as_optional_int(args.get("limit")),
            save=_as_optional_bool(args.get("save"), default=True),
        )

    def _handle_publish_research_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.publish_research(
            title=_require_string(args, "title"),
            body=_require_string(args, "body"),
            question=_as_optional_string(args.get("question")),
            sources=_as_optional_string_list(args.get("sources")),
            tags=_as_optional_string_list(args.get("tags")),
            target=_as_optional_string(args.get("target")),
            dry_run=_as_optional_bool(args.get("dry_run"), default=True),
            visibility=_as_optional_research_publish_visibility(args.get("visibility")) or "internal",
        )

    def _handle_search_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.search(
            _require_string(args, "query"),
            k=_as_optional_int(args.get("k")),
            mode=_as_optional_search_mode(args.get("mode")),
            corpus=_as_optional_search_corpus(args.get("corpus")),
            scope=_as_optional_mapping(args.get("scope")),
            include_content=_as_optional_bool(args.get("include_content")),
            min_relevance_score=_as_optional_float(args.get("min_relevance_score")),
            rerank=_as_optional_rerank_mode(args.get("rerank")),
            debug=_as_optional_bool(args.get("debug")),
        )

    def _handle_digest_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.digest(
            kind=_as_optional_string(args.get("kind")),
            date_selector=_as_optional_string(args.get("date")),
            week_selector=_as_optional_string(args.get("week")),
            from_line=_as_optional_int(args.get("from_line")),
            lines=_as_optional_int(args.get("lines")),
            debug=_as_optional_bool(args.get("debug")),
        )

    def _handle_get_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.get(
            _require_string(args, "path"),
            from_line=_as_optional_int(args.get("from"), default=1),
            lines=_as_optional_int(args.get("lines")),
        )

    def _handle_memory_write_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_write(
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
            agent=_as_optional_string(args.get("agent")),
            session_id=_as_optional_string(args.get("session_id")),
            origin_surface=_as_optional_string(args.get("origin_surface")),
        )

    def _handle_memory_propose_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_propose(
            action=_require_string(args, "action"),
            kind=_require_string(args, "kind"),
            subject=_require_string(args, "subject"),
            content=_require_string(args, "content"),
            scope=_as_optional_string(args.get("scope")),
            confidence=_as_optional_string(args.get("confidence")),
            reason=_as_optional_string(args.get("reason")),
            source=_as_optional_string(args.get("source")),
            soft=_as_optional_bool(args.get("soft"), default=False),
            force_inbox=_as_optional_bool(args.get("force_inbox"), default=False),
            agent=_as_optional_string(args.get("agent")),
            session_id=_as_optional_string(args.get("session_id")),
            origin_surface=_as_optional_string(args.get("origin_surface")),
            source_paths=_as_optional_string_list(args.get("source_paths")),
            proposal_id=_as_optional_string(args.get("proposal_id")),
        )

    def _handle_memory_proposals_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_proposals(status=_as_optional_string(args.get("status")) or "pending")

    def _handle_memory_proposal_get_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_proposal_get(
            _require_string(args, "proposal_id"),
            status=_as_optional_string(args.get("status")) or "pending",
        )

    def _handle_memory_proposal_apply_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_proposal_apply(
            _require_string(args, "proposal_id"),
            agent=_as_optional_string(args.get("agent")),
            session_id=_as_optional_string(args.get("session_id")),
            origin_surface=_as_optional_string(args.get("origin_surface")),
        )

    def _handle_memory_proposal_reject_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.memory_proposal_reject(
            _require_string(args, "proposal_id"),
            reason=_as_optional_string(args.get("reason")),
            agent=_as_optional_string(args.get("agent")),
            session_id=_as_optional_string(args.get("session_id")),
            origin_surface=_as_optional_string(args.get("origin_surface")),
        )

    def _handle_write_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.write(
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
        )

    def _handle_purge_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.purge(
            target=_require_string(args, "target"),
            expected_hash=_as_optional_string(args.get("expected_hash")),
            reason=_as_optional_string(args.get("reason")),
            dry_run=_as_optional_bool(args.get("dry_run"), default=True),
            allow_canonical=_as_optional_bool(args.get("allow_canonical"), default=False),
            include_related_tombstone=_as_optional_bool(
                args.get("include_related_tombstone"),
                default=False,
            ),
        )

    def _handle_link_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.link(dict(args))

    def _handle_status_tool(self, args: dict[str, Any]) -> dict[str, Any]:
        del args
        return self.status()

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
                    agent=self._runtime_agent,
                    session_id=self._session_id or None,
                    origin_surface="hermes-builtin-memory",
                )
                return
            self.write(
                kind="append",
                target=self._memory_mirror_target(),
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
                "description": "Default Dory wake/profile name used for prefetch unless Hermes infers casual from short chat greetings.",
                "default": "coding",
            },
            {
                "key": "active_memory_include_wake",
                "description": "Whether active_memory should include the wake block when Hermes already prefetched wake.",
                "default": False,
            },
            {
                "key": "inject_retrieved_evidence",
                "description": "Append raw search snippets to automatic Hermes context. Disabled by default; active-memory is the injected brief.",
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
        target = _Path(hermes_home) / "dory.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        from config import _as_optional_string as _str_val
        from tools import _as_optional_bool as _bool_val, _as_optional_int as _int_val

        payload = {
            "base_url": _str_val(values.get("base_url")) or _DEFAULT_BASE_URL,
            "default_agent": _str_val(values.get("default_agent")) or "hermes",
            "memory_mode": _str_val(values.get("memory_mode")) or "hybrid",
            "search_mode": _str_val(values.get("search_mode")) or "hybrid",
            "wake_budget_tokens": _int_val(values.get("wake_budget_tokens"), default=600),
            "wake_profile": _str_val(values.get("wake_profile")) or "coding",
            "active_memory_include_wake": _bool_val(
                values.get("active_memory_include_wake"),
                default=False,
            ),
            "inject_retrieved_evidence": _bool_val(values.get("inject_retrieved_evidence"), default=False),
            "search_k": _int_val(values.get("search_k"), default=8),
        }
        target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    # ── public API methods ────────────────────────────────────────────────

    def wake(
        self,
        *,
        agent: str | None = None,
        budget_tokens: int | None = None,
        profile: WakeProfile | None = None,
        project: str | None = None,
        cwd: str | None = None,
        include_recent_sessions: int | None = None,
        include_pinned_decisions: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent": self._resolve_agent(agent),
            "budget_tokens": budget_tokens if budget_tokens is not None else self.wake_budget_tokens,
            "profile": profile if profile is not None else self.wake_profile,
            "include_recent_sessions": (
                include_recent_sessions if include_recent_sessions is not None else self.wake_recent_sessions
            ),
            "include_pinned_decisions": (
                include_pinned_decisions if include_pinned_decisions is not None else self.wake_include_pinned_decisions
            ),
        }
        if project is not None:
            payload["project"] = project
        if cwd is not None:
            payload["cwd"] = cwd
        return self._request("POST", "/v1/wake", json=payload)

    def search(
        self,
        query: str,
        *,
        k: int | None = None,
        mode: SearchMode | None = None,
        corpus: SearchCorpus | None = None,
        scope: dict[str, Any] | None = None,
        include_content: bool | None = None,
        min_relevance_score: float | None = None,
        rerank: str | None = None,
        debug: bool | None = None,
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
        if min_relevance_score is not None:
            payload["min_relevance_score"] = min_relevance_score
        if rerank is not None:
            payload["rerank"] = rerank
        if debug is not None:
            payload["debug"] = debug
        return self._request("POST", "/v1/search", json=payload)

    def digest(
        self,
        *,
        kind: str | None = None,
        date_selector: str | None = None,
        week_selector: str | None = None,
        from_line: int | None = None,
        lines: int | None = None,
        debug: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if kind is not None:
            payload["kind"] = kind
        if date_selector is not None:
            payload["date"] = date_selector
        if week_selector is not None:
            payload["week"] = week_selector
        if from_line is not None:
            payload["from_line"] = from_line
        if lines is not None:
            payload["lines"] = lines
        if debug is not None:
            payload["debug"] = debug
        return self._request("POST", "/v1/digest", json=payload)

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
        agent: str | None = None,
        session_id: str | None = None,
        origin_surface: str | None = None,
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
        if agent is not None:
            payload["agent"] = agent
        if session_id is not None:
            payload["session_id"] = session_id
        if origin_surface is not None:
            payload["origin_surface"] = origin_surface
        return self._request("POST", "/v1/memory-write", json=payload)

    def memory_propose(
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
        force_inbox: bool = False,
        agent: str | None = None,
        session_id: str | None = None,
        origin_surface: str | None = None,
        source_paths: list[str] | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": action,
            "kind": kind,
            "subject": subject,
            "content": content,
            "soft": soft,
            "force_inbox": force_inbox,
        }
        if scope is not None:
            payload["scope"] = scope
        if confidence is not None:
            payload["confidence"] = confidence
        if reason is not None:
            payload["reason"] = reason
        if source is not None:
            payload["source"] = source
        if agent is not None:
            payload["agent"] = agent
        if session_id is not None:
            payload["session_id"] = session_id
        if origin_surface is not None:
            payload["origin_surface"] = origin_surface
        if source_paths:
            payload["source_paths"] = source_paths
        if proposal_id is not None:
            payload["proposal_id"] = proposal_id
        return self._request("POST", "/v1/memory-proposals", json=payload)

    def memory_proposals(self, *, status: str = "pending") -> dict[str, Any]:
        return self._request("POST", "/v1/memory-proposals/list", json={"status": status})

    def memory_proposal_get(self, proposal_id: str, *, status: str = "pending") -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory-proposals/get",
            json={"proposal_id": proposal_id, "status": status},
        )

    def memory_proposal_apply(
        self,
        proposal_id: str,
        *,
        agent: str | None = None,
        session_id: str | None = None,
        origin_surface: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"proposal_id": proposal_id}
        if agent is not None:
            payload["agent"] = agent
        if session_id is not None:
            payload["session_id"] = session_id
        if origin_surface is not None:
            payload["origin_surface"] = origin_surface
        return self._request("POST", "/v1/memory-proposals/apply", json=payload)

    def memory_proposal_reject(
        self,
        proposal_id: str,
        *,
        reason: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        origin_surface: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"proposal_id": proposal_id}
        if reason is not None:
            payload["reason"] = reason
        if agent is not None:
            payload["agent"] = agent
        if session_id is not None:
            payload["session_id"] = session_id
        if origin_surface is not None:
            payload["origin_surface"] = origin_surface
        return self._request("POST", "/v1/memory-proposals/reject", json=payload)

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

    def publish_research(
        self,
        *,
        title: str,
        body: str,
        question: str | None = None,
        sources: list[str] | None = None,
        tags: list[str] | None = None,
        target: str | None = None,
        dry_run: bool | None = True,
        visibility: ResearchPublishVisibility = "internal",
    ) -> dict[str, Any]:
        clean_title = title.strip()
        clean_body = body.strip()
        if not clean_title:
            raise ValueError("publish_research requires a non-empty title")
        if not clean_body:
            raise ValueError("publish_research requires a non-empty body")
        timestamp = datetime.now(timezone.utc)
        target_path = target or f"knowledge/research/{timestamp:%Y-%m-%d-%H%M%S}-{_slugify(clean_title)}.md"
        return self.write(
            kind="create",
            target=target_path,
            content=_render_research_knowledge_note(
                title=clean_title,
                body=clean_body,
                question=question,
                sources=sources or [],
            ),
            dry_run=True if dry_run is None else dry_run,
            frontmatter={
                "title": clean_title,
                "type": "knowledge",
                "status": "done",
                "source_kind": "research",
                "temperature": "warm",
                "visibility": visibility,
                "sensitivity": "none",
                "tags": tags or ["research", "hermes"],
            },
            agent=self._runtime_agent,
            session_id=self._session_id or None,
            reason="publish Hermes research to Dory knowledge",
        )

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
        project: str | None = None,
        scope: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        profile: ActiveMemoryProfile | None = None,
        include_wake: bool | None = None,
        rerank: RerankMode | None = None,
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
        if project is not None:
            payload["project"] = project
        if scope is not None:
            payload["scope"] = scope
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        if rerank is not None:
            payload["rerank"] = rerank
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
        project: str | None = None,
        scope: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        plan = self._prefetch_plan(prompt, project=project, cwd=cwd)
        wake_payload = self.wake(
            agent=agent,
            budget_tokens=budget_tokens,
            profile=plan.profile,
            project=project,
            cwd=cwd,
        )
        search_payload = self.search(prompt, k=k, mode=mode, scope=scope) if plan.include_search else {"results": []}
        active_memory_payload = self.active_memory(
            prompt,
            agent=agent,
            budget_tokens=budget_tokens,
            cwd=cwd,
            project=project,
            scope=scope,
            timeout_ms=timeout_ms,
            profile=plan.profile,
            include_wake=False,
        )
        trace = self._prefetch_trace(
            plan=plan,
            wake_payload=wake_payload,
            search_payload=search_payload,
            active_memory_payload=active_memory_payload,
        )
        return {
            "wake": wake_payload,
            "search": search_payload,
            "active_memory": active_memory_payload,
            "trace": trace.as_dict(),
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
        project: str | None = None,
        scope: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> str:
        prefetched = self.prefetch_bundle(
            prompt,
            agent=agent,
            budget_tokens=budget_tokens,
            k=k,
            mode=mode,
            cwd=cwd,
            project=project,
            scope=scope,
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
        if self.inject_retrieved_evidence and isinstance(results, list) and results:
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
        agent: str | None = None,
        session_id: str | None = None,
        origin_surface: str | None = None,
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
                agent=agent,
                session_id=session_id,
                origin_surface=origin_surface,
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
                    agent=str(write["agent"]) if "agent" in write else None,
                    session_id=str(write["session_id"]) if "session_id" in write else None,
                    origin_surface=str(write["origin_surface"]) if "origin_surface" in write else None,
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

    # ── internal helpers ──────────────────────────────────────────────────

    def _apply_config(self, config: HermesDoryProviderConfig) -> None:
        self.base_url = config.base_url.strip()
        self.token = config.token
        self.default_agent = config.default_agent
        self.wake_budget_tokens = config.wake_budget_tokens
        self.wake_profile = config.wake_profile
        self.wake_recent_sessions = config.wake_recent_sessions
        self.wake_include_pinned_decisions = config.wake_include_pinned_decisions
        self.active_memory_include_wake = config.active_memory_include_wake
        self.inject_retrieved_evidence = config.inject_retrieved_evidence
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
            self._owned_client = httpx.Client(base_url=self.base_url, timeout=30.0)

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
            raise DoryProviderError("Dory provider is not initialized with a base URL or HTTP client")
        response = active_client.request(method, path, json=json, params=params, headers=headers)
        return self._parse_response(response)

    def _session_ingest(self, *, status: SessionStatus, turns: list[SessionTurn]) -> dict[str, Any]:
        if not turns:
            raise DoryProviderError("no session turns available for ingest")
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

    def _memory_mirror_target(self) -> str:
        date_prefix = datetime.now(timezone.utc).date().isoformat()
        return f"inbox/hermes-memory-mirror/{date_prefix}.md"

    def _resolve_agent(self, agent: str | None) -> str:
        if agent is not None and agent.strip():
            return agent.strip()
        if self._runtime_agent.strip():
            return self._runtime_agent.strip()
        return self.default_agent

    def _prefetch_trace(
        self,
        *,
        plan: PrefetchPlan,
        wake_payload: dict[str, Any],
        search_payload: dict[str, Any],
        active_memory_payload: dict[str, Any],
    ) -> PrefetchTrace:
        wake_sources = _string_list(wake_payload.get("sources"))
        active_memory_sources = _string_list(active_memory_payload.get("sources"))
        search_result_paths = _search_result_paths(search_payload)
        injected_paths = _dedupe_strings(active_memory_sources)
        if not injected_paths:
            injected_paths = wake_sources
        if self.inject_retrieved_evidence:
            injected_paths = _dedupe_strings([*injected_paths, *search_result_paths])
        return PrefetchTrace(
            profile=plan.profile,
            include_search=plan.include_search,
            search_skipped=not plan.include_search,
            wake_sources=wake_sources,
            active_memory_sources=active_memory_sources,
            search_result_paths=search_result_paths,
            injected_paths=injected_paths,
        )

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        if response.status_code >= 400:
            from client import _error_type_for_status, _response_error_message

            raise DoryProviderError(
                _response_error_message(response),
                status_code=int(response.status_code),
                error_type=_error_type_for_status(int(response.status_code)),
            )
        return response.json()

    def _prefetch_plan(
        self,
        prompt: str,
        *,
        project: str | None = None,
        cwd: str | None = None,
    ) -> PrefetchPlan:
        if project or cwd:
            return PrefetchPlan(profile="coding", include_search=True)
        if self.wake_profile != "coding":
            return PrefetchPlan(profile=self.wake_profile, include_search=self.wake_profile not in {"casual", "privacy"})
        if self._is_casual_prefetch_prompt(prompt):
            return PrefetchPlan(profile="casual", include_search=False)
        return PrefetchPlan(profile="coding", include_search=True)

    @staticmethod
    def _is_casual_prefetch_prompt(prompt: str) -> bool:
        normalized = " ".join(prompt.strip().lower().split())
        stripped = normalized.strip("!?.,;: ")
        if not stripped:
            return True
        casual_exact = {
            "hi",
            "hey",
            "hello",
            "yo",
            "sup",
            "wassup",
            "what's up",
            "whats up",
            "what up",
            "what's up bro",
            "whats up bro",
            "what's good",
            "whats good",
        }
        return stripped in casual_exact
