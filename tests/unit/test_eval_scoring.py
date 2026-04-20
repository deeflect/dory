from __future__ import annotations

from pathlib import Path

from dory_cli.eval import EvalQuestion, _decide_outcome, _maybe_judge
from dory_core.eval_judge import EvalJudgeDecision


def _question(*, question_type: str = "hot-block") -> EvalQuestion:
    return EvalQuestion(
        id="q-hot",
        question="What machine is OpenClaw running on?",
        path=Path("eval/questions/q-hot.yaml"),
        expected_sources=["core/env.md"],
        expected_keywords=["local workstation", "headless"],
        type=question_type,
        difficulty="easy",
        freshness_sensitive=True,
        task_grounded=False,
        notes="",
    )


def test_hot_block_passes_when_search_evidence_is_strong() -> None:
    outcome = _decide_outcome(
        question=_question(),
        retrieved_paths=["core/env.md"],
        source_hits=1,
        keyword_hits=2,
        is_hot_block=True,
        hot_block_hit=False,
        hot_block_keyword_hits=0,
        search_error=None,
    )

    assert outcome == "passed"


def test_hot_block_is_partial_when_only_hot_block_matches() -> None:
    outcome = _decide_outcome(
        question=_question(),
        retrieved_paths=["knowledge/tools-config/openclaw-best-practices.md"],
        source_hits=0,
        keyword_hits=0,
        is_hot_block=True,
        hot_block_hit=True,
        hot_block_keyword_hits=1,
        search_error=None,
    )

    assert outcome == "partial"


def test_negation_without_expected_sources_uses_judge_when_available() -> None:
    question = EvalQuestion(
        id="q29",
        question="Did we ever commit to Postgres?",
        path=Path("eval/questions/q29.yaml"),
        expected_sources=[],
        expected_keywords=[],
        type="negation",
        difficulty="medium",
        freshness_sensitive=False,
        task_grounded=False,
        notes="Judge-only case.",
    )

    outcome = _decide_outcome(
        question=question,
        retrieved_paths=["knowledge/dev/database-notes.md"],
        source_hits=0,
        keyword_hits=0,
        is_hot_block=False,
        hot_block_hit=False,
        hot_block_keyword_hits=0,
        search_error=None,
        judge_decision=EvalJudgeDecision(outcome="passed", rationale="Evidence supports abstention."),
    )

    assert outcome == "passed"


def test_maybe_judge_skips_standard_questions() -> None:
    question = _question(question_type="entity-recall")

    decision = _maybe_judge(
        judge=None,
        question=question,
        retrieved_paths=["core/env.md"],
        retrieved_snippets=["OpenClaw runs on a local workstation."],
        wake_block=None,
    )

    assert decision is None
