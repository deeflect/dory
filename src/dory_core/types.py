from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


SearchMode = Literal["bm25", "text", "keyword", "lexical", "vector", "semantic", "hybrid", "recall", "exact"]
SearchCorpus = Literal["durable", "sessions", "all"]
WriteKind = Literal["append", "create", "replace", "forget"]
ArtifactKind = Literal["report", "briefing", "wiki-note", "proposal"]
MemoryWriteAction = Literal["write", "replace", "forget"]
MemoryWriteKind = Literal["fact", "preference", "state", "decision", "note"]
WakeProfile = Literal["default", "casual", "coding", "writing", "privacy"]
ActiveMemoryProfile = Literal["auto", "general", "coding", "writing", "privacy", "personal"]


class WakeReq(BaseModel):
    budget_tokens: int = 600
    agent: str
    profile: WakeProfile = "default"
    include_recent_sessions: int = Field(default=5, ge=0)
    include_pinned_decisions: bool = True
    debug: bool = False

    @field_validator("budget_tokens")
    @classmethod
    def clamp_budget(cls, value: int) -> int:
        return min(value, 1500)


class WakeResp(BaseModel):
    profile: WakeProfile = "default"
    tokens_estimated: int
    block: str
    sources: list[str]
    frozen_at: datetime


class SearchScope(BaseModel):
    path_glob: str | None = None
    type: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    since: str | None = None
    until: str | None = None


class SearchReq(BaseModel):
    query: str
    scope: SearchScope = Field(default_factory=SearchScope)
    k: int = Field(default=10, ge=1, le=50)
    mode: SearchMode = "hybrid"
    corpus: SearchCorpus = "durable"
    min_score: float = 0.0
    include_content: bool = True
    rerank: Literal["auto", "true", "false"] = "auto"
    debug: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode_aliases(cls, value: object) -> object:
        if value in {"text", "keyword", "lexical"}:
            return "bm25"
        if value == "semantic":
            return "vector"
        return value


class SearchResult(BaseModel):
    path: str
    lines: str
    score: float
    score_normalized: float | None = None
    rank_score: float | None = None
    evidence_class: Literal["canonical", "generated", "inbox", "session", "raw", "archive", "other"] = "other"
    snippet: str
    frontmatter: dict[str, object] = Field(default_factory=dict)
    stale_warning: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None


class SearchResp(BaseModel):
    query: str
    count: int
    results: list[SearchResult]
    took_ms: int
    warnings: list[str] = Field(default_factory=list)


_SEARCH_RESULT_DEBUG_FIELDS = {"score", "score_normalized", "rank_score", "frontmatter"}
_WAKE_DEBUG_FIELDS = {"tokens_estimated", "sources", "frozen_at"}
_ACTIVE_MEMORY_DEBUG_FIELDS = {"took_ms", "profile", "confidence"}


def serialize_search_response(response: SearchResp, *, debug: bool = False) -> dict[str, Any]:
    payload = response.model_dump(mode="json")
    if debug:
        return payload
    for result in payload.get("results", []):
        if isinstance(result, dict):
            for field in _SEARCH_RESULT_DEBUG_FIELDS:
                result.pop(field, None)
    return payload


def serialize_wake_response(response: WakeResp, *, debug: bool = False) -> dict[str, Any]:
    payload = response.model_dump(mode="json")
    if debug:
        return payload
    for field in _WAKE_DEBUG_FIELDS:
        payload.pop(field, None)
    return payload


class ActiveMemoryReq(BaseModel):
    prompt: str
    agent: str
    cwd: str | None = None
    profile: ActiveMemoryProfile = "auto"
    timeout_ms: int = Field(default=1200, ge=100, le=5000)
    budget_tokens: int = Field(default=400, ge=100, le=1200)
    include_wake: bool = True
    rerank: Literal["auto", "true", "false"] = "auto"
    debug: bool = False


class ActiveMemoryResp(BaseModel):
    kind: Literal["none", "memory"]
    block: str
    summary: str
    took_ms: int = 0
    profile: Literal["general", "coding", "writing", "privacy", "personal"] = "general"
    confidence: Literal["low", "medium", "high"] | None = None
    sources: list[str] = Field(default_factory=list)


def serialize_active_memory_response(response: ActiveMemoryResp, *, debug: bool = False) -> dict[str, Any]:
    payload = response.model_dump(mode="json")
    if debug:
        return payload
    for field in _ACTIVE_MEMORY_DEBUG_FIELDS:
        payload.pop(field, None)
    return payload


class SessionIngestReq(BaseModel):
    path: str
    content: str
    agent: str
    device: str
    session_id: str
    status: Literal["active", "interrupted", "done"]
    captured_from: str
    updated: str


class SessionIngestResp(BaseModel):
    stored: bool
    path: str
    reindexed: bool = False


class WriteReq(BaseModel):
    kind: WriteKind
    target: str
    content: str = ""
    soft: bool = False
    dry_run: bool = False
    frontmatter: dict[str, Any] | None = None
    agent: str | None = None
    session_id: str | None = None
    expected_hash: str | None = None
    reason: str | None = None


class WriteResp(BaseModel):
    path: str
    action: str
    bytes_written: int
    hash: str
    indexed: bool
    edges_added: int = 0


class PurgeReq(BaseModel):
    target: str
    expected_hash: str | None = None
    reason: str | None = None
    dry_run: bool = True
    allow_canonical: bool = False
    include_related_tombstone: bool = False


class PurgeResp(BaseModel):
    path: str
    action: Literal["would_purge", "purged"]
    paths: list[str]
    bytes_deleted: int
    hash: str | None = None
    indexed: bool
    dry_run: bool


class MemoryWriteReq(BaseModel):
    action: MemoryWriteAction
    kind: MemoryWriteKind
    subject: str
    content: str
    scope: Literal["person", "project", "concept", "decision", "core"] | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    reason: str | None = None
    source: str | None = None
    soft: bool = False
    dry_run: bool = False
    force_inbox: bool = False
    allow_canonical: bool = False

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action_aliases(cls, value: object) -> object:
        if value in {"add", "create"}:
            return "write"
        if value in {"remove", "delete"}:
            return "forget"
        return value


class MemoryWriteResp(BaseModel):
    resolved: bool
    action: MemoryWriteAction
    kind: MemoryWriteKind
    subject_ref: str | None = None
    target_path: str | None = None
    result: Literal["preview", "written", "replaced", "forgotten", "quarantined", "rejected"]
    confidence: Literal["high", "medium", "low"] | None = None
    indexed: bool
    quarantined: bool
    message: str | None = None


class RecallEventReq(BaseModel):
    agent: str
    session_key: str | None = None
    query: str
    result_paths: list[str] = Field(default_factory=list)
    selected_path: str | None = None
    corpus: Literal["memory", "sessions", "wiki", "all"] = "memory"
    source: str = "openclaw-recall"


class RecallEventResp(BaseModel):
    stored: bool
    selected_path: str | None = None
    created_at: str | None = None


class LinkReq(BaseModel):
    op: Literal["neighbors", "backlinks", "lint"]
    path: str | None = None
    direction: Literal["out", "in", "both"] = "out"
    depth: int = Field(default=1, ge=1)
    max_edges: int = Field(default=40, ge=1, le=500)
    exclude_prefixes: list[str] = Field(default_factory=list)


class PublicArtifactResp(BaseModel):
    kind: str
    relative_path: str
    content_type: Literal["markdown", "json", "text"]
    title: str | None = None
    agent_ids: list[str] = Field(default_factory=list)


class OpenClawParityDiagnostics(BaseModel):
    flush_enabled: bool
    recall_tracking_enabled: bool
    artifact_listing_enabled: bool
    recent_recall_count: int = 0
    promotion_candidate_count: int = 0
    last_recall_event_at: str | None = None
    last_recall_selected_path: str | None = None
    last_recall_promotion_at: str | None = None
    last_flush_status: str | None = None
    recent_backend_error: str | None = None


class ArtifactReq(BaseModel):
    kind: ArtifactKind
    title: str
    question: str
    body: str
    sources: list[str] = Field(default_factory=list)
    target: str | None = None
    status: Literal["draft", "final"] = "draft"


class ArtifactResp(BaseModel):
    path: str
    kind: ArtifactKind
    bytes_written: int


class ResearchReq(BaseModel):
    question: str
    kind: ArtifactKind = "report"
    corpus: SearchCorpus = "all"
    limit: int = Field(default=8, ge=1, le=20)
    save: bool = True


class ResearchResp(BaseModel):
    artifact: ArtifactReq
    sources: list[str] = Field(default_factory=list)


class MigrateReq(BaseModel):
    legacy_root: str
    use_llm: bool = True


class MigrationStatsResp(BaseModel):
    llm_classified_count: int
    llm_extracted_count: int
    fallback_classified_count: int
    fallback_extracted_count: int
    atom_count: int
    contradiction_count: int
    duration_ms: int


class MigrateResp(BaseModel):
    staged_count: int
    written_count: int
    canonical_created_count: int
    quarantined_count: int
    report_path: str
    run_artifact_path: str | None = None
    stats: MigrationStatsResp | None = None
