from __future__ import annotations

import json
from pathlib import Path

from dory_cli.eval import app


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
