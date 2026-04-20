from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from dory_core.errors import DoryValidationError
from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.metadata import normalize_frontmatter, plan_migration_path


@dataclass(frozen=True, slots=True)
class PlannedDocument:
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


@dataclass(frozen=True, slots=True)
class UnresolvedDocument:
    path: Path
    reason: str


_HEADING_PATTERN = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate a Dory corpus into Bucket Spec v1.")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the planned migration in place.")
    parser.add_argument("--limit", type=int, default=20, help="Max example rows to print per section.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus_root = args.corpus_root.expanduser().resolve()
    planned, unresolved, skipped = build_plan(corpus_root)
    planned = resolve_collisions(planned)

    print_summary(corpus_root, planned, unresolved, skipped, limit=args.limit)

    if not args.apply:
        return 0

    apply_plan(corpus_root, planned)
    prune_empty_directories(corpus_root)
    print(f"applied {len(planned)} document updates")
    return 0


def build_plan(corpus_root: Path) -> tuple[list[PlannedDocument], list[UnresolvedDocument], list[Path]]:
    planned: list[PlannedDocument] = []
    unresolved: list[UnresolvedDocument] = []
    skipped: list[Path] = []

    for source_path in sorted(corpus_root.rglob("*.md")):
        source_rel = source_path.relative_to(corpus_root)
        text = source_path.read_text(encoding="utf-8")
        try:
            document = load_markdown_document(text)
        except ValueError:
            skipped.append(source_rel)
            continue

        repaired_frontmatter = repair_frontmatter(source_rel, document.frontmatter, document.body)
        try:
            migration = plan_migration_path(source_rel, repaired_frontmatter)
        except DoryValidationError as err:
            unresolved.append(UnresolvedDocument(path=source_rel, reason=str(err)))
            continue
        if migration.path is None:
            unresolved.append(
                UnresolvedDocument(
                    path=source_rel,
                    reason=migration.unresolved_reason or "unknown migration failure",
                )
            )
            continue

        try:
            normalized_frontmatter = normalize_frontmatter(repaired_frontmatter, target=migration.path)
        except DoryValidationError as err:
            unresolved.append(UnresolvedDocument(path=source_rel, reason=str(err)))
            continue

        planned.append(
            PlannedDocument(
                source_rel=source_rel,
                target_rel=migration.path,
                frontmatter=normalized_frontmatter,
                body=document.body,
                original_text=text,
            )
        )

    return planned, unresolved, skipped


def repair_frontmatter(source_rel: Path, frontmatter: dict[str, object], body: str) -> dict[str, object]:
    repaired = dict(frontmatter)

    if "title" not in repaired or not str(repaired["title"]).strip():
        repaired["title"] = infer_title(source_rel, body)

    if "type" not in repaired or not str(repaired["type"]).strip():
        inferred_type = infer_type_from_path(source_rel)
        if inferred_type is not None:
            repaired["type"] = inferred_type
    elif str(repaired["type"]).strip().lower() == "archive":
        inferred_type = infer_type_from_path(source_rel)
        if inferred_type is not None:
            repaired["type"] = inferred_type

    return repaired


def infer_title(source_rel: Path, body: str) -> str:
    match = _HEADING_PATTERN.search(body)
    if match is not None:
        return match.group("title").strip()

    stem = source_rel.stem.replace("-", " ").replace("_", " ").strip()
    if stem:
        return " ".join(part.capitalize() for part in stem.split())
    return "Untitled Memory"


def infer_type_from_path(source_rel: Path) -> str | None:
    parts = source_rel.parts
    if not parts:
        return None

    first = parts[0]
    if first == "archive" and len(parts) > 1:
        archive_kind = parts[1]
        archive_type_map = {
            "daily": "daily",
            "drafts": "capture",
            "health-daily": "daily",
            "health-nutrition": "knowledge",
            "ideas": "capture",
            "knowledge": "knowledge",
            "misc": "knowledge",
            "projects": "project",
            "resources": "reference",
            "sessions": "session",
            "tweets": "reference",
            "weekly": "weekly",
        }
        return archive_type_map.get(archive_kind)

    legacy_type_map = {
        "core": "core",
        "daily": "daily",
        "decisions": "decision",
        "inbox": "capture",
        "knowledge": "knowledge",
        "people": "person",
        "projects": "project",
        "resources": "reference",
        "sessions": "session",
        "weekly": "weekly",
    }
    return legacy_type_map.get(first)


def print_summary(
    corpus_root: Path,
    planned: list[PlannedDocument],
    unresolved: list[UnresolvedDocument],
    skipped: list[Path],
    *,
    limit: int,
) -> None:
    changed = [item for item in planned if item.changed]
    moved = [item for item in changed if item.source_rel != item.target_rel]
    rewritten = [item for item in changed if item.source_rel == item.target_rel]

    print(f"corpus_root: {corpus_root}")
    print(f"planned_documents: {len(planned)}")
    print(f"changed_documents: {len(changed)}")
    print(f"moved_documents: {len(moved)}")
    print(f"rewritten_documents: {len(rewritten)}")
    print(f"unresolved_documents: {len(unresolved)}")
    print(f"skipped_non_memory_files: {len(skipped)}")

    if moved:
        print("\nmove_examples:")
        for item in moved[:limit]:
            print(f"  {item.source_rel.as_posix()} -> {item.target_rel.as_posix()}")

    if rewritten:
        print("\nrewrite_examples:")
        for item in rewritten[:limit]:
            print(f"  {item.source_rel.as_posix()}")

    if unresolved:
        print("\nunresolved_examples:")
        for item in unresolved[:limit]:
            print(f"  {item.path.as_posix()}: {item.reason}")

    if skipped:
        print("\nskipped_examples:")
        for item in skipped[:limit]:
            print(f"  {item.as_posix()}")


def resolve_collisions(planned: list[PlannedDocument]) -> list[PlannedDocument]:
    grouped: dict[str, list[PlannedDocument]] = {}
    for item in planned:
        grouped.setdefault(_target_key(item.target_rel), []).append(item)

    resolved: list[PlannedDocument] = []
    for items in grouped.values():
        if len(items) == 1:
            resolved.extend(items)
            continue

        target_rel = items[0].target_rel
        ranked = sorted(items, key=lambda item: (_collision_rank(item.source_rel), item.source_rel.as_posix()))
        resolved.append(ranked[0])
        for variant in ranked[1:]:
            variant_target = build_variant_target(target_rel, variant.source_rel)
            variant_frontmatter = normalize_frontmatter(
                {
                    **variant.frontmatter,
                    "canonical": False,
                    "temperature": "cold",
                },
                target=variant_target,
            )
            resolved.append(
                PlannedDocument(
                    source_rel=variant.source_rel,
                    target_rel=variant_target,
                    frontmatter=variant_frontmatter,
                    body=variant.body,
                    original_text=variant.original_text,
                )
            )

    return sorted(resolved, key=lambda item: item.source_rel.as_posix())


def _collision_rank(source_rel: Path) -> tuple[int, int]:
    if source_rel.parts and source_rel.parts[0] != "archive":
        return (0, len(source_rel.parts))
    return (1, len(source_rel.parts))


def build_variant_target(target_rel: Path, source_rel: Path) -> Path:
    provenance = "-".join(source_rel.parts[:-1]) or "variant"
    suffix = provenance.replace("/", "-")
    return target_rel.parent / "variants" / f"{target_rel.stem}--{suffix}.md"


def _target_key(path: Path) -> str:
    return path.as_posix().casefold()


def apply_plan(corpus_root: Path, planned: list[PlannedDocument]) -> None:
    target_map: dict[str, Path] = {}
    for item in planned:
        key = _target_key(item.target_rel)
        prior = target_map.get(key)
        if prior is not None and prior != item.source_rel:
            raise RuntimeError(
                f"migration target collision: {item.target_rel.as_posix()} from "
                f"{prior.as_posix()} and {item.source_rel.as_posix()}"
            )
        target_map[key] = item.source_rel

    for item in planned:
        source_path = corpus_root / item.source_rel
        target_path = corpus_root / item.target_rel

        if item.source_rel != item.target_rel:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists() and not target_path.samefile(source_path):
                raise RuntimeError(f"target already exists: {item.target_rel.as_posix()}")
            source_path.rename(target_path)
            source_path = target_path
        else:
            source_path.parent.mkdir(parents=True, exist_ok=True)

        source_path.write_text(item.rendered, encoding="utf-8")


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
