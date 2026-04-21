from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import build_runtime_embedder
from dory_core.eval_judge import EvalJudge, EvalJudgeDecision, EvalJudgeRequest, OpenRouterEvalJudge
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.search import SearchEngine
from dory_core.types import SearchReq, WakeReq
from dory_core.wake import WakeBuilder


app = typer.Typer(add_completion=False, help="Run the Dory eval harness.")

DEFAULT_QUESTIONS_ROOT = Path("eval/public/questions")
DEFAULT_RUNS_ROOT = Path("eval/runs")
_HOT_BLOCK_TYPES = {"hot-block"}
_SKIP_SEARCH_TYPES: set[str] = set()


@dataclass(frozen=True, slots=True)
class EvalQuestion:
    id: str
    question: str
    path: Path
    expected_sources: list[str]
    expected_keywords: list[str]
    type: str
    difficulty: str
    freshness_sensitive: bool
    task_grounded: bool
    notes: str


@dataclass(frozen=True, slots=True)
class EvalRun:
    run_id: str
    run_dir: Path
    questions: list[EvalQuestion]
    metrics: dict[str, Any] = field(default_factory=dict)


def load_questions(questions_root: Path, question_id: str | None = None) -> list[EvalQuestion]:
    questions_root = Path(questions_root)
    if question_id is None:
        paths = sorted(questions_root.glob("q*.yaml"))
    else:
        paths = sorted(questions_root.glob(f"{question_id}-*.yaml"))
        if not paths and question_id.endswith(".yaml"):
            paths = [questions_root / question_id]

    return [load_question(path) for path in paths if path.exists()]


def load_question(path: Path) -> EvalQuestion:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"invalid eval question payload in {path}")

    question_id = str(payload.get("id", path.stem.split("-", 1)[0]))
    question = str(payload.get("question", ""))
    if not question:
        raise ValueError(f"missing question text in {path}")

    return EvalQuestion(
        id=question_id,
        question=question,
        path=path,
        expected_sources=_to_str_list(payload.get("expected_sources")),
        expected_keywords=_to_str_list(payload.get("expected_keywords")),
        type=str(payload.get("type", "unknown")),
        difficulty=str(payload.get("difficulty", "unknown")),
        freshness_sensitive=bool(payload.get("freshness_sensitive", False)),
        task_grounded=bool(payload.get("task_grounded", False)),
        notes=str(payload.get("notes", "")),
    )


def run_eval(
    *,
    question_id: str | None = None,
    questions_root: Path = DEFAULT_QUESTIONS_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    corpus_root: Path | None = None,
    index_root: Path | None = None,
    top_k: int = 5,
    score_live: bool = True,
    judge: EvalJudge | None = None,
) -> EvalRun:
    questions = load_questions(questions_root, question_id=question_id)
    if not questions:
        raise ValueError("no eval questions matched the requested input")

    run_id = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    scored: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "top_k": top_k,
        "total": len(questions),
        "passed": 0,
        "failed": 0,
        "partial": 0,
        "skipped": 0,
        "by_type": {},
    }

    engine: SearchEngine | None = None
    wake_block: str | None = None
    wake_sources: list[str] = []
    resolved_corpus_root: Path | None = None

    if score_live:
        paths = resolve_runtime_paths(
            corpus_root=corpus_root,
            index_root=index_root,
        )
        resolved_corpus_root = paths.corpus_root
        settings = DorySettings()
        embedder = build_runtime_embedder()
        query_expander = None
        if settings.query_expansion_enabled and settings.query_expansion_max > 0:
            query_client = build_openrouter_client(settings, purpose="query")
            if query_client is not None:
                query_expander = OpenRouterQueryExpander(
                    client=query_client,
                    max_expansions=settings.query_expansion_max,
                )
        if judge is None and settings.eval_judge_enabled:
            judge_client = build_openrouter_client(settings, purpose="judge")
            if judge_client is not None:
                judge = OpenRouterEvalJudge(client=judge_client)
        engine = SearchEngine(
            paths.index_root,
            embedder,
            query_expander=query_expander,
            reranker=build_reranker(settings),
            rerank_candidate_limit=settings.query_reranker_candidate_limit,
        )
        wake_builder = WakeBuilder(paths.corpus_root)
        wake_result = wake_builder.build(
            WakeReq(
                budget_tokens=settings.default_wake_budget_tokens,
                agent="dory-eval",
                include_recent_sessions=0,
                include_pinned_decisions=True,
            )
        )
        wake_block = wake_result.block
        wake_sources = list(wake_result.sources)

    for question in questions:
        record: dict[str, Any] = {
            "id": question.id,
            "question": question.question,
            "path": str(question.path),
            "type": question.type,
            "difficulty": question.difficulty,
            "expected_sources": question.expected_sources,
            "expected_keywords": question.expected_keywords,
            "freshness_sensitive": question.freshness_sensitive,
            "task_grounded": question.task_grounded,
            "notes": question.notes,
        }

        if not score_live or engine is None:
            record["outcome"] = "skipped"
            metrics["skipped"] += 1
            scored.append(record)
            continue

        started = time.perf_counter()
        retrieved_paths: list[str] = []
        retrieved_snippets: list[str] = []
        search_error: str | None = None
        source_hits = 0
        keyword_hits = 0

        is_hot_block = question.type in _HOT_BLOCK_TYPES
        hot_block_hit = False
        hot_block_keyword_hit = 0

        if is_hot_block and wake_block is not None:
            block_lower = wake_block.lower()
            if question.expected_sources:
                hot_block_hit = any(source in wake_sources for source in question.expected_sources)
            hot_block_keyword_hit = sum(1 for kw in question.expected_keywords if kw.lower() in block_lower)

        try:
            resp = engine.search(SearchReq(query=question.question, k=top_k, mode="hybrid"))
            retrieved_paths = [r.path for r in resp.results]
            retrieved_snippets = [r.snippet for r in resp.results]
        except Exception as err:  # pragma: no cover - captured in record
            search_error = f"{type(err).__name__}: {err}"

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        top_paths_set = set(retrieved_paths)
        source_hits = sum(1 for source in question.expected_sources if source in top_paths_set)
        evidence_parts = list(retrieved_snippets)
        evidence_parts.extend(
            _load_retrieved_source_bodies(
                corpus_root=resolved_corpus_root,
                retrieved_paths=retrieved_paths,
                expected_sources=question.expected_sources,
            )
        )
        if is_hot_block and wake_block is not None:
            evidence_parts.insert(0, wake_block)
        joined_snippets = "\n".join(evidence_parts).lower()
        keyword_hits = sum(1 for kw in question.expected_keywords if kw.lower() in joined_snippets)
        judge_decision = _maybe_judge(
            judge=judge,
            question=question,
            retrieved_paths=retrieved_paths,
            retrieved_snippets=retrieved_snippets,
            wake_block=wake_block if is_hot_block else None,
        )

        record["retrieved_paths"] = retrieved_paths
        record["latency_ms"] = elapsed_ms
        record["source_hits"] = source_hits
        record["keyword_hits"] = keyword_hits
        if search_error:
            record["error"] = search_error
        if is_hot_block:
            record["hot_block_source_hit"] = hot_block_hit
            record["hot_block_keyword_hits"] = hot_block_keyword_hit
        if judge_decision is not None:
            record["judge_outcome"] = judge_decision.outcome
            record["judge_rationale"] = judge_decision.rationale

        outcome = _decide_outcome(
            question=question,
            retrieved_paths=retrieved_paths,
            source_hits=source_hits,
            keyword_hits=keyword_hits,
            is_hot_block=is_hot_block,
            hot_block_hit=hot_block_hit,
            hot_block_keyword_hits=hot_block_keyword_hit,
            search_error=search_error,
            judge_decision=judge_decision,
        )
        record["outcome"] = outcome
        metrics[outcome] = metrics.get(outcome, 0) + 1

        type_bucket = metrics["by_type"].setdefault(
            question.type,
            {"passed": 0, "partial": 0, "failed": 0, "skipped": 0},
        )
        type_bucket[outcome] = type_bucket.get(outcome, 0) + 1

        scored.append(record)

    results = {
        "run_id": run_id,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "question_count": len(questions),
        "top_k": top_k,
        "metrics": metrics,
        "questions": scored,
    }

    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(
        _render_summary(run_id, scored, metrics),
        encoding="utf-8",
    )

    return EvalRun(run_id=run_id, run_dir=run_dir, questions=questions, metrics=metrics)


def _decide_outcome(
    *,
    question: EvalQuestion,
    retrieved_paths: list[str],
    source_hits: int,
    keyword_hits: int,
    is_hot_block: bool,
    hot_block_hit: bool,
    hot_block_keyword_hits: int,
    search_error: str | None,
    judge_decision: EvalJudgeDecision | None = None,
) -> str:
    if search_error:
        return "failed"

    if question.type == "negation" and not question.expected_sources:
        return judge_decision.outcome if judge_decision is not None else "partial"

    if question.type == "temporal" and not question.expected_sources:
        return judge_decision.outcome if judge_decision is not None else "partial"

    expected_sources = len(question.expected_sources)
    expected_keywords = len(question.expected_keywords)

    source_pass = expected_sources == 0 or source_hits >= 1
    keyword_pass = expected_keywords == 0 or keyword_hits >= max(1, expected_keywords // 2)

    if is_hot_block:
        hot_pass = hot_block_hit or hot_block_keyword_hits >= max(1, expected_keywords // 2)
        if source_pass and keyword_pass:
            return "passed"
        if hot_pass and (source_pass or keyword_pass):
            return "passed"
        if hot_pass or source_pass or keyword_pass:
            return "partial"
        return "failed"

    if source_pass and keyword_pass:
        return "passed"
    if source_pass or keyword_pass:
        return "partial"
    return "failed"


def _maybe_judge(
    *,
    judge: EvalJudge | None,
    question: EvalQuestion,
    retrieved_paths: list[str],
    retrieved_snippets: list[str],
    wake_block: str | None,
) -> EvalJudgeDecision | None:
    if judge is None:
        return None
    if question.expected_sources:
        return None
    if question.type not in {"negation", "temporal"}:
        return None
    try:
        return judge.judge(
            EvalJudgeRequest(
                question=question.question,
                question_type=question.type,
                notes=question.notes,
                retrieved_paths=tuple(retrieved_paths),
                retrieved_snippets=tuple(retrieved_snippets),
                wake_block=wake_block,
            )
        )
    except Exception as err:
        return EvalJudgeDecision(
            outcome="partial",
            rationale=f"Judge unavailable: {type(err).__name__}: {err}",
        )


def _load_retrieved_source_bodies(
    *,
    corpus_root: Path | None,
    retrieved_paths: list[str],
    expected_sources: list[str],
) -> list[str]:
    if corpus_root is None:
        return []

    retrieved_set = set(retrieved_paths)
    bodies: list[str] = []
    for source in expected_sources:
        if source not in retrieved_set:
            continue
        path = corpus_root / source
        if not path.exists():
            continue
        try:
            bodies.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return bodies


@app.command("run")
def run_command(
    ctx: typer.Context,
    question_id: str | None = typer.Argument(None, help="Optional question id like q01."),
    questions_root: Path = typer.Option(DEFAULT_QUESTIONS_ROOT, "--questions-root"),
    runs_root: Path = typer.Option(DEFAULT_RUNS_ROOT, "--runs-root"),
    top_k: int = typer.Option(5, "--top-k", help="Top-k chunks per search"),
    list_only: bool = typer.Option(False, "--list-only", help="Skip live search; just scaffold run dir"),
) -> None:
    corpus_root: Path | None = None
    index_root: Path | None = None
    parent_config = getattr(ctx, "obj", None)
    if parent_config is None and ctx.parent is not None:
        parent_config = getattr(ctx.parent, "obj", None)
    if parent_config is not None:
        corpus_root = getattr(parent_config, "corpus_root", None)
        index_root = getattr(parent_config, "index_root", None)

    run = run_eval(
        question_id=question_id,
        questions_root=questions_root,
        runs_root=runs_root,
        top_k=top_k,
        score_live=not list_only,
        corpus_root=corpus_root,
        index_root=index_root,
    )
    typer.echo(str(run.run_dir))
    if run.metrics:
        m = run.metrics
        typer.echo(
            f"passed={m.get('passed', 0)} partial={m.get('partial', 0)} "
            f"failed={m.get('failed', 0)} skipped={m.get('skipped', 0)}"
        )


def _render_summary(
    run_id: str,
    scored: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> str:
    lines = [
        "# Dory eval run",
        "",
        f"- Run ID: {run_id}",
        f"- Questions: {len(scored)}",
        f"- Top-k: {metrics.get('top_k', '?')}",
        "",
        "## Scoreboard",
        "",
        f"- passed: {metrics.get('passed', 0)}",
        f"- partial: {metrics.get('partial', 0)}",
        f"- failed: {metrics.get('failed', 0)}",
        f"- skipped: {metrics.get('skipped', 0)}",
        "",
    ]

    by_type = metrics.get("by_type") or {}
    if by_type:
        lines.append("## By category")
        lines.append("")
        lines.append("| Category | pass | partial | fail | skip |")
        lines.append("|---|---|---|---|---|")
        for cat in sorted(by_type):
            b = by_type[cat]
            lines.append(
                f"| {cat} | {b.get('passed', 0)} | {b.get('partial', 0)} | "
                f"{b.get('failed', 0)} | {b.get('skipped', 0)} |"
            )
        lines.append("")

    lines.append("## Results")
    lines.append("")

    for q in scored:
        icon = {
            "passed": "✅",
            "partial": "🟡",
            "failed": "❌",
            "skipped": "⚪",
        }.get(q.get("outcome", "skipped"), "⚪")
        retrieved = q.get("retrieved_paths") or []
        retrieved_display = ", ".join(retrieved[:3]) if retrieved else "none"
        src_hits = q.get("source_hits", 0)
        kw_hits = q.get("keyword_hits", 0)
        src_total = len(q.get("expected_sources") or [])
        kw_total = len(q.get("expected_keywords") or [])
        latency = q.get("latency_ms")
        err = q.get("error")

        lines.extend(
            [
                f"### {icon} {q['id']} — {q['type']}",
                "",
                q["question"],
                "",
                f"- Sources: {src_hits}/{src_total} hits",
                f"- Keywords: {kw_hits}/{kw_total} hits",
            ]
        )
        if q.get("type") in _HOT_BLOCK_TYPES:
            lines.append(
                f"- Hot block: src_hit={q.get('hot_block_source_hit', False)} "
                f"kw_hits={q.get('hot_block_keyword_hits', 0)}"
            )
        if latency is not None:
            lines.append(f"- Latency: {latency} ms")
        lines.append(f"- Top retrieved: {retrieved_display}")
        if err:
            lines.append(f"- Error: {err}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


if __name__ == "__main__":
    app()
