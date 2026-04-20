from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_validate_module():
    module_path = Path("eval/validate.py").resolve()
    spec = importlib.util.spec_from_file_location("eval_validate_test_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_public_fixture(root: Path) -> None:
    questions_root = root / "eval" / "public" / "questions"
    corpus_root = root / "examples" / "corpus"
    questions_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "projects" / "atlas").mkdir(parents=True, exist_ok=True)

    (questions_root / "q01-atlas-overview.yaml").write_text(
        "\n".join(
            [
                'id: q01',
                'question: "What is Atlas in the public eval suite?"',
                "expected_sources:",
                "  - projects/atlas/state.md",
                "expected_keywords:",
                '  - "Atlas"',
                '  - "public demo"',
                '  - "self-contained"',
                "type: entity-recall",
                "freshness_sensitive: false",
                "task_grounded: false",
                "difficulty: easy",
                "notes: synthetic public-safe question",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (corpus_root / "projects" / "atlas" / "state.md").write_text(
        "\n".join(
            [
                "---",
                "title: Atlas",
                "status: active",
                "---",
                "",
                "Atlas is the public demo project for the eval suite.",
                "It is intentionally self-contained.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_validate_uses_public_defaults_when_repo_contains_public_suite(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_validate_module()
    _write_public_fixture(tmp_path)

    module.REPO_ROOT = tmp_path
    module.DEFAULT_QUESTIONS_ROOT = tmp_path / "eval" / "public" / "questions"
    module.DEFAULT_CORPUS_ROOT = tmp_path / "examples" / "corpus"
    module.SPEC_ROOT = tmp_path

    exit_code = module.main([])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    assert "All questions valid" in captured.out


def test_validate_accepts_custom_roots_and_reports_keyword_failures(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_validate_module()

    questions_root = tmp_path / "questions"
    corpus_root = tmp_path / "corpus"
    questions_root.mkdir(parents=True)
    corpus_root.mkdir(parents=True)

    (questions_root / "q01-custom.yaml").write_text(
        "\n".join(
            [
                'id: q01',
                'question: "What lives in the custom corpus?"',
                "expected_sources:",
                "  - docs/example.md",
                "expected_keywords:",
                '  - "missing keyword"',
                "type: entity-recall",
                "freshness_sensitive: false",
                "task_grounded: false",
                "difficulty: easy",
                "notes: custom root test",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (corpus_root / "docs").mkdir(parents=True)
    (corpus_root / "docs" / "example.md").write_text("this file does not mention the keyword\n", encoding="utf-8")

    exit_code = module.main(
        [
            "--questions-root",
            str(questions_root),
            "--corpus-root",
            str(corpus_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "expected_keywords not found" in captured.out


def test_validate_fails_when_no_questions_exist(tmp_path: Path, capsys) -> None:
    module = _load_validate_module()
    questions_root = tmp_path / "questions"
    corpus_root = tmp_path / "corpus"
    questions_root.mkdir()
    corpus_root.mkdir()

    exit_code = module.main(
        [
            "--questions-root",
            str(questions_root),
            "--corpus-root",
            str(corpus_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no question files found" in captured.out


def test_validate_rejects_non_string_keywords(tmp_path: Path, capsys) -> None:
    module = _load_validate_module()

    questions_root = tmp_path / "questions"
    corpus_root = tmp_path / "corpus"
    questions_root.mkdir()
    (corpus_root / "docs").mkdir(parents=True)
    (corpus_root / "docs" / "example.md").write_text("alpha\n", encoding="utf-8")
    (questions_root / "q01-custom.yaml").write_text(
        "\n".join(
            [
                "id: q01",
                'question: "What is alpha?"',
                "expected_sources:",
                "  - docs/example.md",
                "expected_keywords:",
                "  - 123",
                "type: entity-recall",
                "freshness_sensitive: false",
                "task_grounded: false",
                "difficulty: easy",
                "notes: custom root test",
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--questions-root",
            str(questions_root),
            "--corpus-root",
            str(corpus_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "expected_keywords entry not a string" in captured.out
