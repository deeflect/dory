from __future__ import annotations

import json
from pathlib import Path

from dory_cli.eval import app, run_eval
from dory_core.index.reindex import reindex_corpus


def test_eval_runner_writes_summary(
    cli_runner,
    tmp_path: Path,
) -> None:
    questions_root = Path("eval/public/questions")
    runs_root = tmp_path / "runs"

    result = cli_runner.invoke(
        app,
        [
            "q01",
            "--questions-root",
            str(questions_root),
            "--runs-root",
            str(runs_root),
            "--list-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip()

    run_dir = Path(result.stdout.strip().splitlines()[0])
    assert run_dir.exists()

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")

    assert results["run_id"] == run_dir.name
    assert results["question_count"] == 1
    assert results["questions"][0]["id"] == "q01"
    assert "q01" in summary
    assert "What is Atlas in the public eval suite?" in summary


def test_public_eval_q05_records_source_hit_for_current_beacon_state(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = Path("examples/corpus")
    index_root = tmp_path / ".index"
    runs_root = tmp_path / "runs"
    reindex_corpus(corpus_root, index_root, fake_embedder)

    run = run_eval(
        question_id="q05",
        questions_root=Path("eval/public/questions"),
        runs_root=runs_root,
        corpus_root=corpus_root,
        index_root=index_root,
        top_k=5,
        score_live=True,
    )

    results = json.loads((run.run_dir / "results.json").read_text(encoding="utf-8"))
    question = results["questions"][0]
    assert question["id"] == "q05"
    assert question["source_hits"] >= 1
    assert question["keyword_hits"] >= 2
    assert "projects/beacon/state.md" in question["retrieved_paths"][:5]
    assert question["outcome"] == "passed"
