from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Callable, Iterable, Literal

from dory_core.config import DorySettings
from dory_core.llm.openrouter import (
    OpenRouterModelMetadata,
    OpenRouterModelPricing,
    OpenRouterPurpose,
    resolve_openrouter_model_metadata,
)
from dory_core.migration_prompts import (
    build_classification_system_prompt,
    build_classification_user_prompt,
    build_document_extraction_system_prompt,
    build_document_extraction_user_prompt,
    build_extraction_system_prompt,
    build_extraction_user_prompt,
)
from dory_core.token_counting import TokenCounter, build_token_counter

SelectionMode = Literal["full", "folders", "sample"]
_IGNORED_SCAN_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    "__pycache__",
    ".pytest_cache",
}


@dataclass(frozen=True, slots=True)
class MigrationFolderStat:
    folder: str
    markdown_count: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class MigrationCorpusScan:
    corpus_root: Path
    markdown_files: tuple[Path, ...]
    folder_stats: tuple[MigrationFolderStat, ...]
    markdown_count: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class MigrationScope:
    selected_roots: tuple[str, ...] = ()
    sample_size: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_roots", _normalize_selected_roots(self.selected_roots))
        if self.sample_size is not None and self.sample_size < 1:
            raise ValueError("sample_size must be at least 1")

    @property
    def selection_mode(self) -> SelectionMode:
        if self.sample_size is not None:
            return "sample"
        if self.selected_roots:
            return "folders"
        return "full"


@dataclass(frozen=True, slots=True)
class MigrationEstimate:
    model: str
    model_name: str | None
    pricing: OpenRouterModelPricing | None
    pricing_source: Literal["env", "live", "none"]
    classification_input_tokens: int
    classification_output_tokens: int
    extraction_input_tokens: int
    extraction_output_tokens: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_input_usd: float | None
    estimated_output_usd: float | None
    estimated_total_usd: float | None


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    scan: MigrationCorpusScan
    scope: MigrationScope
    selected_markdown_files: tuple[Path, ...]
    selected_markdown_count: int
    selected_byte_count: int
    preview_files: tuple[Path, ...]
    estimate: MigrationEstimate


@dataclass(frozen=True, slots=True)
class MigrationPlanner:
    settings: DorySettings | None = None
    token_counter: TokenCounter = field(default_factory=build_token_counter)
    model_purpose: OpenRouterPurpose = "maintenance"
    live_pricing: bool = False
    classification_output_tokens: int = 120
    extraction_output_tokens: int = 180
    preview_limit: int = 8
    metadata_resolver: MetadataResolver = field(
        default=resolve_openrouter_model_metadata
    )

    def scan_corpus(self, corpus_root: Path) -> MigrationCorpusScan:
        corpus_root = Path(corpus_root)
        markdown_files = tuple(
            sorted(
                path
                for path in corpus_root.rglob("*.md")
                if path.is_file() and not any(part in _IGNORED_SCAN_PARTS for part in path.relative_to(corpus_root).parts)
            )
        )
        folder_counts: dict[str, int] = {}
        folder_bytes: dict[str, int] = {}
        total_bytes = 0
        for path in markdown_files:
            folder = _top_level_bucket(corpus_root, path)
            folder_counts[folder] = folder_counts.get(folder, 0) + 1
            size = path.stat().st_size
            folder_bytes[folder] = folder_bytes.get(folder, 0) + size
            total_bytes += size
        folder_stats = tuple(
            MigrationFolderStat(folder=folder, markdown_count=folder_counts[folder], byte_count=folder_bytes[folder])
            for folder in sorted(folder_counts)
        )
        return MigrationCorpusScan(
            corpus_root=corpus_root,
            markdown_files=markdown_files,
            folder_stats=folder_stats,
            markdown_count=len(markdown_files),
            byte_count=total_bytes,
        )

    def build_plan(self, scan: MigrationCorpusScan, *, scope: MigrationScope | None = None) -> MigrationPlan:
        resolved_scope = scope or MigrationScope()
        selected_files = self._select_files(scan, resolved_scope)
        selected_bytes = sum(path.stat().st_size for path in selected_files)
        estimate = self._estimate_selected_files(scan, selected_files)
        preview_files = tuple(selected_files[: self.preview_limit])
        return MigrationPlan(
            scan=scan,
            scope=resolved_scope,
            selected_markdown_files=selected_files,
            selected_markdown_count=len(selected_files),
            selected_byte_count=selected_bytes,
            preview_files=preview_files,
            estimate=estimate,
        )

    def plan_corpus(
        self,
        corpus_root: Path,
        *,
        scope: MigrationScope | None = None,
    ) -> MigrationPlan:
        scan = self.scan_corpus(corpus_root)
        return self.build_plan(scan, scope=scope)

    def _select_files(self, scan: MigrationCorpusScan, scope: MigrationScope) -> tuple[Path, ...]:
        files = scan.markdown_files
        if scope.selected_roots:
            allowed = set(scope.selected_roots)
            files = tuple(path for path in files if _top_level_bucket(scan.corpus_root, path) in allowed)
        if scope.sample_size is not None and scope.sample_size < len(files):
            files = _sample_evenly(files, scope.sample_size)
        return files

    def _estimate_selected_files(self, scan: MigrationCorpusScan, files: Iterable[Path]) -> MigrationEstimate:
        metadata = self.metadata_resolver(self.settings, purpose=self.model_purpose, use_live_pricing=self.live_pricing)
        classification_input_tokens = 0
        extraction_input_tokens = 0
        selected_files = tuple(files)
        for path in selected_files:
            text = path.read_text(encoding="utf-8")
            rel_path = path.relative_to(scan.corpus_root).as_posix()
            classification_input_tokens += self.token_counter.count(build_document_extraction_system_prompt())
            classification_input_tokens += self.token_counter.count(
                build_document_extraction_user_prompt(path=rel_path, text=text)
            )

        classification_output_tokens = 0
        extraction_input_tokens = 0
        extraction_output_tokens = len(selected_files) * (self.classification_output_tokens + self.extraction_output_tokens)
        estimated_input_tokens = classification_input_tokens + extraction_input_tokens
        estimated_output_tokens = classification_output_tokens + extraction_output_tokens
        estimated_total_tokens = estimated_input_tokens + estimated_output_tokens

        estimated_input_usd, estimated_output_usd, estimated_total_usd = _estimate_costs(
            pricing=metadata.pricing,
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
        )
        return MigrationEstimate(
            model=metadata.model,
            model_name=metadata.name,
            pricing=metadata.pricing,
            pricing_source=metadata.pricing_source,
            classification_input_tokens=classification_input_tokens,
            classification_output_tokens=classification_output_tokens,
            extraction_input_tokens=extraction_input_tokens,
            extraction_output_tokens=extraction_output_tokens,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            estimated_total_tokens=estimated_total_tokens,
            estimated_input_usd=estimated_input_usd,
            estimated_output_usd=estimated_output_usd,
            estimated_total_usd=estimated_total_usd,
        )


def _top_level_bucket(corpus_root: Path, path: Path) -> str:
    rel = path.relative_to(corpus_root)
    if len(rel.parts) <= 1:
        return "root"
    return rel.parts[0]


def _normalize_selected_roots(roots: Iterable[str] | None) -> tuple[str, ...]:
    if not roots:
        return ()
    normalized = []
    for root in roots:
        value = str(root).strip().strip("/").strip("\\").lower()
        if not value:
            continue
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _sample_evenly(files: tuple[Path, ...], sample_size: int) -> tuple[Path, ...]:
    if sample_size >= len(files):
        return files
    if sample_size == 1:
        return (files[len(files) // 2],)
    step = max(1, ceil(len(files) / sample_size))
    sampled = files[::step]
    if len(sampled) >= sample_size:
        return sampled[:sample_size]
    chosen = list(sampled)
    seen = set(chosen)
    for path in files:
        if path in seen:
            continue
        chosen.append(path)
        seen.add(path)
        if len(chosen) == sample_size:
            break
    return tuple(chosen[:sample_size])


def _estimate_costs(
    *,
    pricing: OpenRouterModelPricing | None,
    input_tokens: int,
    output_tokens: int,
) -> tuple[float | None, float | None, float | None]:
    if pricing is None:
        return None, None, None
    input_cost = (input_tokens / 1_000_000) * pricing.input_usd_per_million
    output_cost = (output_tokens / 1_000_000) * pricing.output_usd_per_million
    total_cost = input_cost + output_cost
    return input_cost, output_cost, total_cost
MetadataResolver = Callable[..., OpenRouterModelMetadata]
