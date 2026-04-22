from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from watchdog.observers import Observer

from dory_cli.eval import run_eval
from dory_core.claim_store import ClaimRecord, ClaimStore
from dory_core.claims import Claim, EvidenceRef
from dory_core.dreaming.events import SessionClosedEvent
from dory_core.dreaming.extract import DistillationWriter, OpenRouterSessionDistiller
from dory_core.dreaming.proposals import ProposalGenerator
from dory_core.dreaming.recall import RecallPromotionRunner
from dory_core.embedding import ContentEmbedder
from dory_core.compiled_wiki import render_compiled_page, render_compiled_page_from_claim_records
from dory_core.index.reindex import ReindexResult, reindex_corpus, reindex_paths
from dory_core.llm.json_client import JSONGenerationClient
from dory_core.llm.openrouter import OpenRouterClient
from dory_core.maintenance import MaintenanceReportWriter, OpenRouterMaintenanceInspector
from dory_core.maintenance import MemoryHealthDashboard
from dory_core.frontmatter import load_markdown_document
from dory_core.wiki_indexes import WikiIndexBuilder
from dory_core.session_sync import sync_session_files
from dory_core.watch import BufferedMarkdownChangeHandler, WatchCoalescer, is_session_markdown


@dataclass(frozen=True, slots=True)
class DreamScan:
    session_paths: tuple[str, ...]
    digest_paths: tuple[str, ...]
    distilled_paths: tuple[str, ...]
    proposal_paths: tuple[str, ...]
    recall_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DreamOnceResult:
    distilled: tuple[str, ...]
    proposed: tuple[str, ...]
    recall_distilled: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MaintenanceOnceResult:
    reports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvalOnceResult:
    reindex: ReindexResult | None
    run_id: str
    run_dir: str
    summary_path: str
    results_path: str
    metrics: dict[str, object]


@dataclass(frozen=True, slots=True)
class CompiledWikiRoute:
    target_rel: Path
    entity_id: str | None


class DreamOnceRunner:
    def __init__(
        self,
        root: Path,
        client: JSONGenerationClient,
        *,
        index_root: Path | None = None,
        backend: str = "openrouter",
    ) -> None:
        self.root = Path(root)
        self.index_root = Path(index_root) if index_root is not None else self.root / ".dory" / "index"
        self.client = client
        self.backend = backend
        self.writer = DistillationWriter(self.root)

    def collect_candidates(self, *, include_sessions: bool = False, min_session_age_seconds: float = 0) -> DreamScan:
        session_paths: list[str] = []
        digest_paths: list[str] = []
        distilled_paths: list[str] = []
        proposal_paths: list[str] = []
        recall_runner = RecallPromotionRunner.create(root=self.root, index_root=self.index_root)
        now = datetime.now(tz=UTC)
        if include_sessions:
            for session_file in sorted((self.root / "logs" / "sessions").rglob("*.md")):
                if min_session_age_seconds > 0:
                    modified_at = datetime.fromtimestamp(session_file.stat().st_mtime, tz=UTC)
                    if (now - modified_at).total_seconds() < min_session_age_seconds:
                        continue
                session_rel = str(session_file.relative_to(self.root))
                if not self._distilled_target_for_session(session_rel).exists():
                    session_paths.append(session_rel)
        for digest_file in self._iter_digest_files():
            digest_rel = str(digest_file.relative_to(self.root))
            if not self._proposal_target_for_source(digest_file).exists():
                digest_paths.append(digest_rel)
        for distilled_file in sorted((self.root / "inbox" / "distilled").glob("*.md")):
            distilled_rel = str(distilled_file.relative_to(self.root))
            if not self._proposal_target_for_source(distilled_file).exists():
                distilled_paths.append(distilled_rel)
        recall_paths = tuple(
            str(recall_runner.writer.relative_output_path(candidate))
            for candidate in recall_runner.collect_candidates()
        )
        distilled_paths.extend(path for path in recall_paths if path not in distilled_paths)
        return DreamScan(
            session_paths=tuple(session_paths),
            digest_paths=tuple(digest_paths),
            distilled_paths=tuple(distilled_paths),
            proposal_paths=tuple(proposal_paths),
            recall_paths=tuple(recall_paths),
        )

    def run(
        self,
        session_paths: Iterable[str] | None = None,
        *,
        limit: int | None = None,
        min_session_age_seconds: float = 0,
    ) -> DreamOnceResult:
        requested_sessions = set(session_paths or ())
        scan = self.collect_candidates(
            include_sessions=bool(requested_sessions),
            min_session_age_seconds=min_session_age_seconds,
        )
        distilled: list[str] = []
        proposed: list[str] = []
        distiller = OpenRouterSessionDistiller(client=self.client, writer=self.writer)
        generator = ProposalGenerator(root=self.root, backend=self.backend, client=self.client)
        recall_distilled = RecallPromotionRunner.create(root=self.root, index_root=self.index_root).run()
        distilled.extend(path for path in recall_distilled if path not in distilled)

        processed_sessions = 0
        for session_rel in scan.session_paths:
            if requested_sessions and session_rel not in requested_sessions:
                continue
            if limit is not None and processed_sessions >= limit:
                break
            session_file = self.root / session_rel
            event = SessionClosedEvent(
                agent=_infer_agent_from_session_path(session_rel),
                session_path=session_rel,
                closed_at=datetime.fromtimestamp(session_file.stat().st_mtime, tz=UTC),
            )
            target = distiller.distill(event, session_file.read_text(encoding="utf-8"))
            distilled.append(str(target.relative_to(self.root)))
            processed_sessions += 1

        proposal_candidates = list(scan.digest_paths)
        proposal_candidates.extend(path for path in scan.distilled_paths if path not in proposal_candidates)
        proposal_candidates.extend(path for path in recall_distilled if path not in proposal_candidates)
        proposal_candidates.extend(distilled)
        deduped_candidates: list[str] = []
        seen_candidates: set[str] = set()
        for distilled_rel in proposal_candidates:
            if distilled_rel in seen_candidates:
                continue
            deduped_candidates.append(distilled_rel)
            seen_candidates.add(distilled_rel)
        processed_proposals = 0
        for distilled_rel in deduped_candidates:
            if limit is not None and processed_proposals >= limit:
                break
            distilled_file = self.root / distilled_rel
            if not distilled_file.exists():
                continue
            target = generator.generate(distilled_file)
            proposed.append(str(target.relative_to(self.root)))
            processed_proposals += 1

        return DreamOnceResult(
            distilled=tuple(distilled),
            proposed=tuple(proposed),
            recall_distilled=tuple(recall_distilled),
        )

    def _distilled_target_for_session(self, session_rel: str) -> Path:
        agent = _infer_agent_from_session_path(session_rel)
        event = SessionClosedEvent.now(agent=agent, session_path=session_rel)
        return self.root / event.output_path

    def _proposal_target_for_source(self, source_file: Path) -> Path:
        return self.root / "inbox" / "proposed" / f"{source_file.stem}.json"

    def _iter_digest_files(self) -> Iterable[Path]:
        for digest_root in (self.root / "digests" / "daily", self.root / "digests" / "weekly"):
            if digest_root.exists():
                yield from sorted(digest_root.glob("*.md"))


class MaintenanceOnceRunner:
    def __init__(self, root: Path, client: OpenRouterClient) -> None:
        self.root = Path(root)
        self.inspector = OpenRouterMaintenanceInspector(client=client)
        self.writer = MaintenanceReportWriter(self.root)

    def default_targets(self) -> tuple[str, ...]:
        targets: list[str] = []
        for path in sorted((self.root / "core").glob("*.md")):
            targets.append(str(path.relative_to(self.root)))
        for path in sorted((self.root / "people").glob("*.md")):
            targets.append(str(path.relative_to(self.root)))
        for path in sorted((self.root / "projects").glob("*/state.md")):
            targets.append(str(path.relative_to(self.root)))
        for path in sorted((self.root / "concepts").glob("*.md")):
            targets.append(str(path.relative_to(self.root)))
        for path in sorted((self.root / "decisions").glob("*.md")):
            targets.append(str(path.relative_to(self.root)))
        return tuple(targets)

    def run(self, targets: Iterable[str] | None = None) -> MaintenanceOnceResult:
        selected = tuple(targets or self.default_targets())
        reports: list[str] = []
        for target_rel in selected:
            path = self.root / target_rel
            if not path.exists():
                continue
            report = self.inspector.inspect(target_rel, path.read_text(encoding="utf-8"))
            report_path = self.writer.write(report)
            reports.append(str(report_path.relative_to(self.root)))
        return MaintenanceOnceResult(reports=tuple(reports))


class WikiHealthRunner:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dashboard = MemoryHealthDashboard(self.root)

    def run(self, *, write_report: bool = False) -> dict[str, object]:
        report = self.dashboard.inspect()
        payload: dict[str, object] = {"report": report}
        if write_report:
            target = self.dashboard.write_report()
            payload["report_path"] = str(target.relative_to(self.root))
        return payload


def run_compiled_wiki_refresh(corpus_root: Path) -> list[str]:
    root = Path(corpus_root)
    written: list[str] = []
    claim_store = _load_claim_store(root)
    expected_targets: set[Path] = set()
    for source_rel in _compiled_wiki_sources(root):
        source = root / source_rel
        if not source.exists():
            continue
        compiled = _compiled_wiki_page(root, source, source_rel, claim_store=claim_store)
        if compiled is None:
            continue
        target_rel, page_text = compiled
        expected_targets.add(target_rel)
        target = root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_text, encoding="utf-8")
        written.append(target_rel.as_posix())
    written.extend(path for path in _prune_stale_compiled_wiki_pages(root, expected_targets) if path not in written)
    written.extend(path for path in WikiIndexBuilder(root).refresh() if path not in written)
    return written


def run_wiki_index_refresh(corpus_root: Path) -> list[str]:
    return WikiIndexBuilder(corpus_root).refresh()


class EvalOnceRunner:
    def __init__(self, corpus_root: Path, index_root: Path, embedder: ContentEmbedder) -> None:
        self.corpus_root = Path(corpus_root)
        self.index_root = Path(index_root)
        self.embedder = embedder

    def run(
        self,
        *,
        reindex_first: bool = True,
        questions_root: Path = Path("eval/public/questions"),
        runs_root: Path = Path("eval/runs"),
        top_k: int = 5,
    ) -> EvalOnceResult:
        reindex_result = None
        if reindex_first:
            reindex_result = reindex_corpus(self.corpus_root, self.index_root, self.embedder)
        run = run_eval(
            corpus_root=self.corpus_root,
            index_root=self.index_root,
            questions_root=questions_root,
            runs_root=runs_root,
            top_k=top_k,
            score_live=True,
        )
        return EvalOnceResult(
            reindex=reindex_result,
            run_id=run.run_id,
            run_dir=str(run.run_dir),
            summary_path=str(run.run_dir / "summary.md"),
            results_path=str(run.run_dir / "results.json"),
            metrics=run.metrics,
        )


class OpsWatchRunner:
    def __init__(
        self,
        *,
        corpus_root: Path,
        index_root: Path,
        embedder: ContentEmbedder,
        debounce_seconds: float = 1.0,
        dream_runner: DreamOnceRunner | None = None,
    ) -> None:
        self.corpus_root = Path(corpus_root)
        self.index_root = Path(index_root)
        self.embedder = embedder
        self.dream_runner = dream_runner
        self.coalescer = WatchCoalescer(debounce_seconds=debounce_seconds)
        self.handler = BufferedMarkdownChangeHandler(self.coalescer)

    def process_pending(self) -> dict[str, object] | None:
        if not self.coalescer.ready():
            return None
        changed_paths = self.coalescer.drain()
        durable_candidates: list[str] = []
        session_candidates: list[str] = []
        for path in changed_paths:
            candidate = Path(path)
            if candidate.suffix.lower() != ".md":
                continue
            try:
                relative_candidate = str(candidate.resolve().relative_to(self.corpus_root.resolve()))
            except ValueError:
                continue
            if is_session_markdown(candidate, root=self.corpus_root):
                session_candidates.append(relative_candidate)
            else:
                durable_candidates.append(relative_candidate)
        if durable_candidates:
            reindex_result = reindex_paths(self.corpus_root, self.index_root, self.embedder, durable_candidates)
        else:
            reindex_result = ReindexResult(files_indexed=0, chunks_indexed=0, vectors_indexed=0)
        session_sync = (
            sync_session_files(self.corpus_root, self.index_root / "session_plane.db", session_candidates)
            if session_candidates
            else None
        )
        payload: dict[str, object] = {
            "changed_paths": changed_paths,
            "reindex": asdict(reindex_result),
        }
        if session_sync is not None:
            payload["session_sync"] = asdict(session_sync)
        if self.dream_runner is not None:
            digest_candidates = [
                str(Path(path).resolve().relative_to(self.corpus_root.resolve()))
                for path in changed_paths
                if self._is_digest_markdown(Path(path))
            ]
            if digest_candidates:
                payload["dream"] = asdict(self.dream_runner.run())
        return payload

    def serve_forever(self, *, poll_interval: float = 0.25) -> None:
        observer = Observer()
        observer.schedule(self.handler, str(self.corpus_root), recursive=True)
        observer.start()
        try:
            while True:
                payload = self.process_pending()
                if payload is not None:
                    print(serialize_result(payload), flush=True)
                time.sleep(poll_interval)
        finally:
            observer.stop()
            observer.join()

    def _is_digest_markdown(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.corpus_root.resolve())
        except ValueError:
            return False
        parts = relative.parts
        return len(parts) >= 3 and parts[0] == "digests" and parts[1] in {"daily", "weekly"} and path.suffix == ".md"


def serialize_result(payload: object) -> str:
    return json.dumps(
        asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload, indent=2, sort_keys=True
    )


def _infer_agent_from_session_path(session_path: str) -> str:
    parts = Path(session_path).parts
    if len(parts) >= 3 and parts[0] == "logs" and parts[1] == "sessions":
        return parts[2]
    return "codex"


def _compiled_wiki_sources(root: Path) -> list[str]:
    sources: list[str] = []
    active = root / "core" / "active.md"
    if active.exists():
        sources.append("core/active.md")
    sources.extend(str(path.relative_to(root)) for path in sorted((root / "people").glob("*.md")))
    sources.extend(str(path.relative_to(root)) for path in sorted((root / "projects").glob("*/state.md")))
    sources.extend(str(path.relative_to(root)) for path in sorted((root / "concepts").glob("*.md")))
    sources.extend(str(path.relative_to(root)) for path in sorted((root / "decisions").glob("*.md")))
    return sources


def _compiled_wiki_page(
    root: Path,
    source: Path,
    source_rel: str,
    *,
    claim_store: ClaimStore | None,
) -> tuple[Path, str] | None:
    try:
        document = load_markdown_document(source.read_text(encoding="utf-8"))
    except ValueError:
        return None

    title = _compiled_wiki_title(document.frontmatter, source)
    route = _compiled_wiki_route(source_rel=source_rel, title=title, source=source)
    claim_records = _claim_records_for_route(claim_store, route)
    claim_events = claim_store.claim_events(route.entity_id) if claim_store is not None and route.entity_id else ()
    summary = _compiled_wiki_summary(document.body) or _compiled_wiki_claim_summary(claim_records)
    if not summary and not claim_records:
        return None
    if claim_records:
        page = render_compiled_page_from_claim_records(
            title=title,
            summary=summary,
            claim_records=claim_records,
            claim_events=claim_events,
            contradictions=(),
            open_questions=(),
            last_refreshed=datetime.now(tz=UTC).date().isoformat(),
        )
        return route.target_rel, page

    claim = Claim(
        id=_compiled_wiki_slug(title),
        statement=summary,
        status="confirmed",
        confidence="high",
        freshness="fresh",
        sources=(
            EvidenceRef(
                path=source_rel,
                line="1:1",
                surface="durable",
                note="Compiled from canonical source",
            ),
        ),
        last_reviewed=datetime.now(tz=UTC).isoformat(),
    )
    page = render_compiled_page(
        title=title,
        summary=summary,
        claims=(claim,),
        contradictions=(),
        open_questions=(),
        last_refreshed=datetime.now(tz=UTC).date().isoformat(),
    )
    return route.target_rel, page


def _compiled_wiki_title(frontmatter: dict[str, object], source: Path) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    stem = source.stem.replace("-", " ").replace("_", " ")
    return stem.title()


def _compiled_wiki_summary(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:280]
    return ""


def _compiled_wiki_claim_summary(claim_records: tuple[ClaimRecord, ...]) -> str:
    for record in claim_records:
        statement = record.statement.strip()
        if record.status == "active" and statement:
            return statement[:280]
    for record in reversed(claim_records):
        statement = record.statement.strip()
        if statement:
            return statement[:280]
    return ""


def _compiled_wiki_route(*, source_rel: str, title: str, source: Path) -> CompiledWikiRoute:
    if source_rel == "core/active.md":
        slug = _compiled_wiki_slug(title)
        return CompiledWikiRoute(
            target_rel=Path("wiki/projects") / f"{slug}.md",
            entity_id=f"project:{slug}",
        )
    if source_rel.startswith("projects/"):
        slug = source.parent.name.strip() or _compiled_wiki_slug(title)
        return CompiledWikiRoute(
            target_rel=Path("wiki/projects") / f"{_compiled_wiki_slug(title)}.md",
            entity_id=f"project:{slug}",
        )
    if source_rel.startswith("decisions/"):
        slug = source.stem.strip() or _compiled_wiki_slug(title)
        return CompiledWikiRoute(
            target_rel=Path("wiki/decisions") / f"{_compiled_wiki_slug(title)}.md",
            entity_id=f"decision:{slug}",
        )
    if source_rel.startswith("people/"):
        slug = source.stem.strip() or _compiled_wiki_slug(title)
        return CompiledWikiRoute(
            target_rel=Path("wiki/people") / f"{_compiled_wiki_slug(title)}.md",
            entity_id=f"person:{slug}",
        )
    if source_rel.startswith("concepts/"):
        slug = source.stem.strip() or _compiled_wiki_slug(title)
        return CompiledWikiRoute(
            target_rel=Path("wiki/concepts") / f"{_compiled_wiki_slug(title)}.md",
            entity_id=f"concept:{slug}",
        )
    slug = source.stem.strip() or _compiled_wiki_slug(title)
    return CompiledWikiRoute(
        target_rel=Path("wiki/concepts") / f"{_compiled_wiki_slug(title)}.md",
        entity_id=f"concept:{slug}",
    )


def _claim_records_for_route(
    claim_store: ClaimStore | None,
    route: CompiledWikiRoute,
) -> tuple[ClaimRecord, ...]:
    if claim_store is None or route.entity_id is None:
        return ()
    return claim_store.claim_history(route.entity_id)


def _load_claim_store(root: Path) -> ClaimStore | None:
    claim_store_path = root / ".dory" / "claim-store.db"
    if not claim_store_path.exists():
        return None
    return ClaimStore(claim_store_path)


def _prune_stale_compiled_wiki_pages(root: Path, expected_targets: set[Path]) -> list[str]:
    removed: list[str] = []
    for family in ("people", "projects", "concepts", "decisions"):
        family_root = root / "wiki" / family
        if not family_root.exists():
            continue
        for path in sorted(family_root.glob("*.md")):
            if path.name == "index.md":
                continue
            if path.relative_to(root) in expected_targets:
                continue
            if not _is_generated_wiki_page(path):
                continue
            path.unlink()
            removed.append(path.relative_to(root).as_posix())
    return removed


def _is_generated_wiki_page(path: Path) -> bool:
    try:
        document = load_markdown_document(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return str(document.frontmatter.get("source_kind", "")).strip().lower() == "generated"


def _compiled_wiki_slug(text: str) -> str:
    parts = [part.lower() for part in re.findall(r"[A-Za-z0-9]+", text)]
    return "-".join(parts) or "compiled"
