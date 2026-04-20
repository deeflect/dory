#!/usr/bin/env python3
"""
validate.py — check that every eval question references real corpus files
and that expected_keywords actually appear in at least one expected_source file.

By default this validates the public synthetic suite:
    eval/public/questions/ against examples/corpus/

Use flags to validate a private canonical suite:
    python3 eval/validate.py --questions-root /path/to/private/questions --corpus-root /path/to/private/corpus

Exits 0 if all questions pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections.abc import Sequence

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml")
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = REPO_ROOT / "examples" / "corpus"
SPEC_ROOT = REPO_ROOT
DEFAULT_QUESTIONS_ROOT = REPO_ROOT / "eval" / "public" / "questions"


def resolve(path_str: str, *, corpus_root: Path, spec_root: Path) -> Path:
    """Resolve a relative path against the corpus root, or repo root for specs/."""
    p = Path(path_str)
    if p.parts and p.parts[0] == "specs":
        return spec_root / p
    return corpus_root / p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate eval question files against a corpus tree.")
    parser.add_argument(
        "--questions-root",
        type=Path,
        default=DEFAULT_QUESTIONS_ROOT,
        help="Root directory containing eval question YAML files.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=DEFAULT_CORPUS_ROOT,
        help="Root directory containing the corpus referenced by questions.",
    )
    parser.add_argument(
        "--spec-root",
        type=Path,
        default=SPEC_ROOT,
        help="Root directory used to resolve specs/ references.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    questions_root = Path(args.questions_root)
    corpus_root = Path(args.corpus_root)
    spec_root = Path(args.spec_root)

    if not questions_root.is_dir():
        print(f"ERROR: {questions_root} does not exist")
        return 2

    failures: list[str] = []
    warnings: list[str] = []
    total = 0

    question_paths = sorted(questions_root.glob("q*.yaml"))
    if not question_paths:
        print(f"ERROR: no question files found in {questions_root}")
        return 1

    for yaml_path in question_paths:
        total += 1
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{yaml_path.name}: YAML parse error: {exc}")
            continue

        if not isinstance(data, dict):
            failures.append(f"{yaml_path.name}: top-level is not a mapping")
            continue

        qid = str(data.get("id", "")).strip()
        if not qid:
            failures.append(f"{yaml_path.name}: missing id")
        elif not yaml_path.name.startswith(qid + "-"):
            failures.append(f"{yaml_path.name}: id {qid!r} does not match filename prefix")

        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            failures.append(f"{yaml_path.name}: missing question")

        qtype = data.get("type")
        if not isinstance(qtype, str):
            failures.append(f"{yaml_path.name}: missing type")

        expected_sources = data.get("expected_sources") or []
        if not isinstance(expected_sources, list):
            failures.append(f"{yaml_path.name}: expected_sources must be a list")
            expected_sources = []

        if not expected_sources and qtype not in {"negation", "temporal"}:
            failures.append(f"{yaml_path.name}: expected_sources is empty")

        resolved_existing: list[Path] = []
        for src in expected_sources:
            if not isinstance(src, str):
                failures.append(f"{yaml_path.name}: expected_sources entry not a string")
                continue
            p = resolve(src, corpus_root=corpus_root, spec_root=spec_root)
            if "*" in src:
                matches = list(p.parent.glob(p.name))
                if not matches:
                    failures.append(f"{yaml_path.name}: no match for glob {src}")
                else:
                    resolved_existing.extend(matches)
            elif not p.exists():
                failures.append(f"{yaml_path.name}: expected_source missing on disk: {src}")
            else:
                resolved_existing.append(p)

        expected_keywords = data.get("expected_keywords") or []
        if not isinstance(expected_keywords, list):
            failures.append(f"{yaml_path.name}: expected_keywords must be a list")
            expected_keywords = []
        else:
            for kw in expected_keywords:
                if not isinstance(kw, str):
                    failures.append(f"{yaml_path.name}: expected_keywords entry not a string")

        if expected_keywords and resolved_existing:
            combined_text = "\n".join(
                p.read_text(encoding="utf-8", errors="replace").lower() for p in resolved_existing
            )
            missing_keywords = [
                kw for kw in expected_keywords if isinstance(kw, str) and kw.lower() not in combined_text
            ]
            if missing_keywords:
                failures.append(f"{yaml_path.name}: expected_keywords not found in sources: {missing_keywords}")

    print(f"Checked {total} question files.")
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    if failures:
        print(f"\nFailures ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll questions valid ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
