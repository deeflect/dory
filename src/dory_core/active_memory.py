from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Protocol

from dory_core.active_memory_admission import ObservationRetrievalBackend, admit_observations
from dory_core.active_memory_policy import SourcePolicy, filter_active_memory_results, source_policy_for_request
from dory_core.active_memory_render import (
    WikiHelperContext,
    build_summary,
    confidence_for_results,
    empty_wiki_helper_context,
    load_wiki_helper_context,
    planning_context_from_helper,
    synthesized_bullets,
    topic_scoped_helper_context,
    wake_block_for_rendering,
)
from dory_core.active_memory_retrieval import (
    active_memory_rerank_mode,
    preferred_active_memory_results,
    search_candidates,
    suppress_global_context_for_entity,
    with_project_result,
)
from dory_core.entity_context import (
    EntityContext,
    resolve_default_entity_context,
)
from dory_core.hot_context import (
    HotContextPacket,
    SourceBackedItem,
    dedupe_sources,
    render_packet_to_block,
    source_backed_items_from_results,
)
from dory_core.markdown_excerpt import (
    result_evidence_text,
    truncate_text,
)

from dory_core.profiles import ProfileRegistry
from dory_core.project_context import resolve_project_handle
from dory_core.retrieval_planner import (
    ActiveMemoryComposition,
    ActiveMemoryComposer,
    ActiveMemoryPlanningContext,
    ActiveMemoryPlanner,
    ActiveMemoryRetrievalPlan,
    fallback_active_memory_plan,
)
from dory_core.types import (
    ActiveMemoryReq,
    ActiveMemoryResp,
    SearchScope,
    WakeReq,
    WakeResp,
)

_logger = logging.getLogger(__name__)
_COMPOSER_SNIPPET_CHARS = 360
_SESSION_COMPOSER_SNIPPET_CHARS = 180


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    """Configurable budget and timeout thresholds for active memory stages."""

    planner_min_remaining_ms: int = 1800
    composer_min_remaining_ms: int = 2200
    composer_timeout_headroom_ms: int = 6000
    rerank_timeout_headroom_ms: int = 6000
    partial_ok: bool = True


class _WakeBuilder(Protocol):
    def build(self, req: WakeReq) -> WakeResp: ...


class _SearchEngine(Protocol):
    def search(self, req: object) -> object: ...


@dataclass(slots=True)
class ActiveMemoryEngine:
    wake_builder: _WakeBuilder
    search_engine: _SearchEngine
    root: Path | None = None
    observation_retrieval: ObservationRetrievalBackend | None = None
    planner: ActiveMemoryPlanner | None = None
    composer: ActiveMemoryComposer | None = None
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    def build(self, req: ActiveMemoryReq) -> ActiveMemoryResp:
        started = monotonic()
        deadline = _Deadline.from_timeout_ms(req.timeout_ms)
        profile_registry = ProfileRegistry(self.root or Path("."))
        source_policy = source_policy_for_request(req, profile_registry=profile_registry)

        helper = empty_wiki_helper_context()
        wake_block = ""
        wake_sources: list[str] = []
        durable_results: list[object] = []
        session_results: list[object] = []
        entity_context: EntityContext | None = None

        try:
            # Slice 5: resolve deterministic entity context before any
            # broad retrieval.  This gives downstream code (planning,
            # search scoping) a typed entity packet without calling an LLM.
            if req.resolve_entity_context and self.root is not None:
                entity_context = self._resolve_entity_context(req, source_policy)
            helper = self._helper_context(req, source_policy, entity_context)
            wake_block, wake_sources = self._wake_context(req, source_policy)
            planning_context = self._planning_context(helper, entity_context)
            plan = self._plan(req, planning_context, deadline=deadline)
            durable_results, session_results = self._retrieve_evidence(req, plan, source_policy, deadline=deadline)
            durable_results = with_project_result(
                req,
                durable_results,
                root=self.root,
                source_policy=source_policy,
                entity_context=entity_context,
            )
            durable_results = suppress_global_context_for_entity(durable_results, entity_context)
            renderable_durable_results = preferred_active_memory_results(durable_results)
            composition = self._trusted_composition(
                req,
                planning_context,
                wake_block,
                renderable_durable_results,
                session_results,
                deadline=deadline,
            )
            return self._response_from_context(
                req,
                source_policy,
                started=started,
                deadline=deadline,
                helper=helper,
                wake_block=wake_block,
                wake_sources=wake_sources,
                durable_results=durable_results,
                session_results=session_results,
                composition=composition,
                entity_context=entity_context,
            )
        except Exception:
            if not self.budget.partial_ok or not req.partial_ok:
                raise
            _logger.exception("active-memory build failed; returning partial response")
            return self._response_from_context(
                req,
                source_policy,
                started=started,
                deadline=deadline,
                helper=helper,
                wake_block=wake_block,
                wake_sources=wake_sources,
                durable_results=durable_results,
                session_results=session_results,
                composition=None,
                warnings=["Active memory retrieval encountered an error; returning partial context."],
                entity_context=entity_context,
            )

    def _response_from_context(
        self,
        req: ActiveMemoryReq,
        source_policy: SourcePolicy,
        *,
        started: float,
        deadline: "_Deadline",
        helper: WikiHelperContext,
        wake_block: str,
        wake_sources: list[str],
        durable_results: list[object],
        session_results: list[object],
        composition: ActiveMemoryComposition | None,
        warnings: list[str] | None = None,
        entity_context: EntityContext | None = None,
    ) -> ActiveMemoryResp:
        partial_warnings = warnings or []
        renderable_durable_results = preferred_active_memory_results(durable_results)
        evidence_root = self.root if deadline.total_ms > self.budget.composer_timeout_headroom_ms else None
        syn_bullets = synthesized_bullets(
            helper,
            renderable_durable_results,
            session_results,
            root=evidence_root,
        )
        memory_bullets = (
            list(composition.bullets) if composition is not None and composition.bullets else syn_bullets
        )
        summary = (
            composition.summary
            if composition is not None and composition.summary
            else build_summary(helper, renderable_durable_results, session_results, wake_block, root=evidence_root)
        )
        packet = self._build_packet(
            req=req,
            source_policy=source_policy,
            helper=helper,
            wake_block=wake_block,
            wake_sources=wake_sources,
            renderable_durable_results=renderable_durable_results,
            session_results=session_results,
            partial_warnings=partial_warnings,
            entity_context=entity_context,
            memory_bullets=memory_bullets,
            evidence_root=evidence_root,
        )
        sources = list(packet.sources)
        block = render_packet_to_block(
            packet,
            budget_tokens=req.budget_tokens,
        )
        conf = confidence_for_results(renderable_durable_results, session_results)
        ctx_dict: dict[str, object] | None = None
        if entity_context is not None:
            ctx_dict = {
                "entity_id": entity_context.entity_id,
                "canonical_name": entity_context.canonical_name,
                "family": entity_context.family,
                "canonical_path": entity_context.canonical_path,
                "matched_by": entity_context.matched_by,
                "source_refs": list(entity_context.source_refs),
            }
        return ActiveMemoryResp(
            kind="memory" if block else "none",
            block=block,
            summary=summary,
            took_ms=max(1, int((monotonic() - started) * 1000)),
            profile=source_policy.profile,
            confidence=conf,
            sources=sources,
            partial=bool(partial_warnings),
            warnings=list(packet.warnings),
            entity_context=ctx_dict,
        )

    def _build_packet(
        self,
        *,
        req: ActiveMemoryReq,
        source_policy: SourcePolicy,
        helper: WikiHelperContext,
        wake_block: str,
        wake_sources: list[str],
        renderable_durable_results: list[object],
        session_results: list[object],
        partial_warnings: list[str],
        entity_context: EntityContext | None = None,
        memory_bullets: list[str] | None = None,
        evidence_root: Path | None = None,
    ) -> HotContextPacket:
        """Assemble a typed ``HotContextPacket`` from the scattered internal data.

        This is the canonical internal data carrier for active-memory
        rendering.  External response types (``ActiveMemoryResp``) are
        produced *from* this packet; they are not replaced by it.
        """
        rendered_wake_block = wake_block_for_rendering(wake_block, renderable_durable_results, session_results)

        # Build source-backed items from search results.
        durable_items = source_backed_items_from_results(
            renderable_durable_results,
            max_items=4,
            root=evidence_root,
        )
        session_items = source_backed_items_from_results(
            session_results,
            max_items=3,
            snippet_chars=160,
            root=evidence_root,
        )

        # Active-memory response rendering uses the packet's active claims.
        active_claims: list[SourceBackedItem] = [
            SourceBackedItem(text=truncate_text(bullet, 220)) for bullet in memory_bullets or []
        ]
        observation_admission = admit_observations(
            observation_retrieval=self.observation_retrieval,
            entity_context=entity_context,
            source_policy=source_policy,
            max_items=2,
        )
        observations = observation_admission.items

        # Guardrails from source policy.
        guardrails: list[str] = []
        if source_policy.profile == "privacy":
            guardrails.append("privacy boundaries active")
        if source_policy.retrieval.sessions == "never":
            guardrails.append("session context excluded")

        return HotContextPacket(
            profile=source_policy.profile,
            guardrails=tuple(guardrails),
            project=entity_context,
            entity_context=(entity_context,) if entity_context is not None else (),
            active_claims=tuple(active_claims),
            observations=observations,
            durable_evidence=durable_items,
            session_evidence=session_items,
            sources=dedupe_sources(
                wake_sources if rendered_wake_block else [],
                [str(getattr(r, "path", "")) for r in renderable_durable_results[:4]],
                [str(getattr(r, "path", "")) for r in session_results[:3]],
                [item.source_path or "" for item in observations],
            ),
            warnings=(*partial_warnings, *observation_admission.warnings),
            partial=bool(partial_warnings),
            wake_context=(SourceBackedItem(text=rendered_wake_block),) if rendered_wake_block else (),
        )

    def _resolve_entity_context(self, req: ActiveMemoryReq, source_policy: SourcePolicy) -> EntityContext | None:
        if self.root is None:
            return None
        if req.project:
            return resolve_default_entity_context(
                project=req.project,
                cwd=req.cwd,
                root=self.root,
            )
        if req.cwd and _allows_cwd_project_inference(req, source_policy):
            return resolve_default_entity_context(
                project=None,
                cwd=req.cwd,
                root=self.root,
            )
        return None

    def _helper_context(
        self,
        req: ActiveMemoryReq,
        source_policy: SourcePolicy,
        entity_context: EntityContext | None,
    ) -> WikiHelperContext:
        if entity_context is not None:
            return empty_wiki_helper_context()
        helper = (
            load_wiki_helper_context(self.root)
            if source_policy.retrieval.use_helper_context
            else empty_wiki_helper_context()
        )
        return topic_scoped_helper_context(helper, prompt=req.prompt, source_policy=source_policy)

    def _planning_context(
        self,
        helper: WikiHelperContext,
        entity_context: EntityContext | None,
    ) -> ActiveMemoryPlanningContext:
        context = planning_context_from_helper(helper)
        if entity_context is None:
            return context
        return ActiveMemoryPlanningContext(
            current_focus=context.current_focus,
            recent_pages=context.recent_pages,
            active_threads=context.active_threads,
            index_hints=context.index_hints,
            entity_names=(entity_context.canonical_name, entity_context.entity_id),
            entity_source_refs=entity_context.source_refs,
        )

    def _wake_context(self, req: ActiveMemoryReq, source_policy: SourcePolicy) -> tuple[str, list[str]]:
        if not req.include_wake:
            return "", []
        wake = self.wake_builder.build(
            WakeReq(
                budget_tokens=min(req.budget_tokens, 600),
                agent=req.agent,
                profile=source_policy.retrieval.wake_profile,
                project=self._wake_project_handle(req, source_policy),
                include_recent_sessions=3 if source_policy.include_session_context else 0,
                include_pinned_decisions=source_policy.retrieval.include_pinned_decisions,
            )
        )
        return wake.block, wake.sources

    def _wake_project_handle(self, req: ActiveMemoryReq, source_policy: SourcePolicy) -> str | None:
        if req.project:
            return resolve_project_handle(project=req.project, cwd=req.cwd, root=self.root)
        if req.cwd and _allows_cwd_project_inference(req, source_policy):
            return resolve_project_handle(project=None, cwd=req.cwd, root=self.root)
        return None

    def _retrieve_evidence(
        self,
        req: ActiveMemoryReq,
        plan: ActiveMemoryRetrievalPlan,
        source_policy: SourcePolicy,
        *,
        deadline: "_Deadline",
    ) -> tuple[list[object], list[object]]:
        durable_results = filter_active_memory_results(
            search_candidates(
                self.search_engine,
                queries=plan.durable_queries,
                k=plan.durable_limit,
                mode="hybrid",
                corpus="durable",
                include_content=True,
                rerank=active_memory_rerank_mode(req.rerank, deadline, self.budget),
                deadline=deadline,
                source_policy=source_policy,
                min_remaining_ms=self.budget.composer_min_remaining_ms,
            ),
            corpus="durable",
            source_policy=source_policy,
        )
        session_results = filter_active_memory_results(
            self._retrieve_session_evidence(plan, source_policy, session_scope=req.scope, deadline=deadline),
            corpus="sessions",
            source_policy=source_policy,
        )
        return durable_results, session_results

    def _retrieve_session_evidence(
        self,
        plan: ActiveMemoryRetrievalPlan,
        source_policy: SourcePolicy,
        *,
        session_scope: SearchScope,
        deadline: "_Deadline",
    ) -> list[object]:
        if not source_policy.include_session_context or not plan.include_sessions or plan.session_limit <= 0:
            return []
        return search_candidates(
            self.search_engine,
            queries=plan.session_queries,
            k=plan.session_limit,
            mode="recall",
            corpus="sessions",
            include_content=False,
            rerank="false",
            scope=session_scope,
            deadline=deadline,
            source_policy=source_policy,
            min_remaining_ms=self.budget.composer_min_remaining_ms,
        )

    def _trusted_composition(
        self,
        req: ActiveMemoryReq,
        context: ActiveMemoryPlanningContext,
        wake_block: str,
        durable_results: list[object],
        session_results: list[object],
        *,
        deadline: "_Deadline",
    ) -> ActiveMemoryComposition | None:
        composition = self._compose(req, context, wake_block, durable_results, session_results, deadline=deadline)
        if _composition_conflicts_with_evidence(composition, durable_results):
            return None
        return composition

    def _plan(
        self,
        req: ActiveMemoryReq,
        context: ActiveMemoryPlanningContext,
        *,
        deadline: "_Deadline",
    ) -> ActiveMemoryRetrievalPlan:
        if self.planner is None or deadline.remaining_ms < self.budget.planner_min_remaining_ms:
            return fallback_active_memory_plan(prompt=req.prompt)
        try:
            return self.planner.plan_active_memory(prompt=req.prompt, context=context)
        except Exception:
            _logger.exception("active-memory planner failed; using deterministic fallback plan")
            return fallback_active_memory_plan(prompt=req.prompt)

    def _compose(
        self,
        req: ActiveMemoryReq,
        context: ActiveMemoryPlanningContext,
        wake_block: str,
        durable_results: list[object],
        session_results: list[object],
        *,
        deadline: "_Deadline",
    ) -> ActiveMemoryComposition | None:
        if not durable_results and not session_results:
            return None
        if (
            self.composer is None
            or deadline.remaining_ms < self.budget.composer_min_remaining_ms
            or deadline.total_ms <= self.budget.composer_timeout_headroom_ms
        ):
            return None
        try:
            return self.composer.compose_active_memory(
                prompt=req.prompt,
                context=context,
                wake_summary=_first_non_empty_line(wake_block),
                durable_results=tuple(
                    (
                        _result_path(item),
                        truncate_text(result_evidence_text(item, root=self.root), _COMPOSER_SNIPPET_CHARS),
                    )
                    for item in durable_results[:4]
                ),
                session_results=tuple(
                    (
                        _result_path(item),
                        truncate_text(result_evidence_text(item, root=self.root), _SESSION_COMPOSER_SNIPPET_CHARS),
                    )
                    for item in session_results[:2]
                ),
            )
        except Exception:
            _logger.exception("active-memory composer failed; using deterministic synthesis")
            return None


@dataclass(frozen=True, slots=True)
class _Deadline:
    expires_at: float
    total_ms: int

    @classmethod
    def from_timeout_ms(cls, timeout_ms: int) -> "_Deadline":
        return cls(expires_at=monotonic() + (timeout_ms / 1000), total_ms=timeout_ms)

    @property
    def expired(self) -> bool:
        return monotonic() >= self.expires_at

    @property
    def remaining_ms(self) -> int:
        return max(0, int((self.expires_at - monotonic()) * 1000))


def _result_path(result: object) -> str:
    return str(getattr(result, "path", "") or "")


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        value = item.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _allows_cwd_project_inference(req: ActiveMemoryReq, source_policy: SourcePolicy) -> bool:
    if source_policy.profile in {"coding", "admin"}:
        return True
    agent = req.agent.strip().casefold()
    return agent in {"codex", "claude", "claude-code", "opencode", "openclaw"}


def _composition_conflicts_with_evidence(composition: object | None, durable_results: list[object]) -> bool:
    if composition is None:
        return False
    has_active_source = any(_result_path(result) == "core/active.md" for result in durable_results)
    if not has_active_source:
        return False
    summary = str(getattr(composition, "summary", "") or "").lower()
    bullets = " ".join(str(item) for item in getattr(composition, "bullets", ()) or ()).lower()
    text = f"{summary} {bullets}"
    return any(
        phrase in text
        for phrase in (
            "no active focus",
            "no active project",
            "no active work",
            "no current focus",
            "no designated task",
        )
    )
