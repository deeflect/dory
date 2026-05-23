from __future__ import annotations

from pathlib import Path

import typer

from dory_core.eval_runner import (
    DEFAULT_QUESTIONS_ROOT,
    DEFAULT_RUNS_ROOT,
    EvalQuestion,
    EvalRun,
    load_question,
    load_questions,
    run_eval,
)

# Re-export for backward compatibility
__all__ = [
    "DEFAULT_QUESTIONS_ROOT",
    "DEFAULT_RUNS_ROOT",
    "EvalQuestion",
    "EvalRun",
    "app",
    "load_question",
    "load_questions",
    "run_eval",
]

app = typer.Typer(add_completion=False, help="Run the Dory eval harness.")


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


if __name__ == "__main__":
    app()
