from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


from dory_core.frontmatter import dump_markdown_document, load_markdown_document


@dataclass(frozen=True, slots=True)
class PlannedUpdate:
    source_rel: Path
    target_rel: Path
    frontmatter: dict[str, object]
    body: str
    original_text: str

    @property
    def rendered(self) -> str:
        return dump_markdown_document(self.frontmatter, self.body)

    @property
    def changed(self) -> bool:
        return self.source_rel != self.target_rel or self.rendered != self.original_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize corpus metadata without inventing facts.")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply planned updates in place.")
    parser.add_argument("--limit", type=int, default=20, help="Number of examples to print.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus_root = args.corpus_root.expanduser().resolve()
    planned = build_plan(corpus_root)
    changed = [item for item in planned if item.changed]

    print(f"corpus_root: {corpus_root}")
    print(f"planned_documents: {len(planned)}")
    print(f"changed_documents: {len(changed)}")
    print(f"moved_documents: {sum(1 for item in changed if item.source_rel != item.target_rel)}")
    print(f"rewritten_documents: {sum(1 for item in changed if item.source_rel == item.target_rel)}")

    if changed:
        print("\nexamples:")
        for item in changed[: args.limit]:
            if item.source_rel == item.target_rel:
                print(f"  rewrite {item.source_rel.as_posix()}")
            else:
                print(f"  move {item.source_rel.as_posix()} -> {item.target_rel.as_posix()}")

    if not args.apply:
        return 0

    apply_plan(corpus_root, planned)
    prune_empty_directories(corpus_root)
    return 0


def build_plan(corpus_root: Path) -> list[PlannedUpdate]:
    planned: list[PlannedUpdate] = []
    for source_path in sorted(corpus_root.rglob("*.md")):
        source_rel = source_path.relative_to(corpus_root)
        if source_rel.name in {"README.md", "MANIFEST.md"}:
            continue

        text = source_path.read_text(encoding="utf-8")
        document = load_markdown_document(text)
        frontmatter = dict(document.frontmatter)
        target_rel = normalize_target(source_rel)
        normalize_area(frontmatter, target_rel)
        planned.append(
            PlannedUpdate(
                source_rel=source_rel,
                target_rel=target_rel,
                frontmatter=frontmatter,
                body=document.body,
                original_text=text,
            )
        )
    return planned


def normalize_target(source_rel: Path) -> Path:
    if source_rel.parts and source_rel.parts[0] == "sessions":
        return Path("logs") / source_rel
    if source_rel.parts[:2] == ("inbox", "overnight-research"):
        return Path("knowledge") / "business" / "overnight-research" / source_rel.name
    return source_rel


def normalize_area(frontmatter: dict[str, object], target_rel: Path) -> None:
    normalize_bucket_semantics(frontmatter, target_rel)
    if frontmatter.get("area") not in {None, ""}:
        return

    parts = target_rel.parts
    area = infer_area(parts)
    if area is not None:
        frontmatter["area"] = area


def infer_area(parts: tuple[str, ...]) -> str | None:
    if parts[:2] == ("knowledge", "dev"):
        return "coding"
    if parts[:3] == ("knowledge", "business", "x-content-strategy"):
        return "marketing"
    if parts[:2] == ("knowledge", "personal"):
        return "personal"
    if "health-nutrition" in parts:
        return "health"
    return None


def normalize_bucket_semantics(frontmatter: dict[str, object], target_rel: Path) -> None:
    parts = target_rel.parts
    if parts[:2] == ("logs", "daily") and frontmatter.get("type") == "session":
        frontmatter["type"] = "daily"
        if frontmatter.get("status") == "raw":
            frontmatter["status"] = "done"
    if parts[:3] == ("knowledge", "business", "overnight-research") and frontmatter.get("type") == "capture":
        frontmatter["type"] = "knowledge"


def apply_plan(corpus_root: Path, planned: list[PlannedUpdate]) -> None:
    for item in planned:
        source_path = corpus_root / item.source_rel
        target_path = corpus_root / item.target_rel

        if item.source_rel != item.target_rel:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists() and not target_path.samefile(source_path):
                raise RuntimeError(f"target already exists: {item.target_rel.as_posix()}")
            source_path.rename(target_path)
            source_path = target_path

        rendered = item.rendered
        if rendered != item.original_text or item.source_rel != item.target_rel:
            source_path.write_text(rendered, encoding="utf-8")


def prune_empty_directories(corpus_root: Path) -> None:
    directories = sorted(
        (path for path in corpus_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        if directory == corpus_root:
            continue
        try:
            next(directory.iterdir())
        except StopIteration:
            directory.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
