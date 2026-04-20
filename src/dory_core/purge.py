from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import load_markdown_document
from dory_core.fs import resolve_corpus_target
from dory_core.index.reindex import reindex_paths
from dory_core.types import PurgeReq, PurgeResp

_VALID_TARGET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-/]*\.md$")
_DEFAULT_PURGE_ROOTS = (
    "inbox/",
    "sources/semantic/",
    "logs/evals/",
    "eval/",
    "tmp/",
)
_PROTECTED_ROOTS = (
    "core/",
    "people/",
    "projects/",
    "decisions/canonical/",
)


@dataclass(frozen=True, slots=True)
class PurgeEngine:
    root: Path
    index_root: Path | None = None
    embedder: ContentEmbedder | None = None

    def purge(self, req: PurgeReq) -> PurgeResp:
        target_rel = _validate_target(req.target)
        target = resolve_corpus_target(self.root, target_rel)
        if not target.exists():
            raise DoryValidationError(f"target does not exist: {req.target}")
        if not target.is_file():
            raise DoryValidationError(f"target is not a file: {req.target}")

        current_text = target.read_text(encoding="utf-8")
        current_hash = f"sha256:{sha256(current_text.encode('utf-8')).hexdigest()}"
        protected = _is_protected(target_rel, current_text)
        if protected and not req.allow_canonical:
            raise DoryValidationError("purge of canonical/protected memory requires allow_canonical=true")
        if not protected and not _is_default_purge_path(target_rel) and not req.allow_canonical:
            raise DoryValidationError(
                "purge outside inbox/, sources/semantic/, logs/evals/, eval/, or tmp/ requires allow_canonical=true"
            )
        if not req.dry_run:
            if not req.reason:
                raise DoryValidationError("live purge requires a reason")
            if req.expected_hash is None or req.expected_hash != current_hash:
                raise DoryValidationError("live purge requires a matching expected_hash")

        purge_paths = self._collect_purge_paths(target_rel, include_related_tombstone=req.include_related_tombstone)
        bytes_deleted = sum((self.root / path).stat().st_size for path in purge_paths if (self.root / path).exists())
        if req.dry_run:
            return PurgeResp(
                path=target_rel.as_posix(),
                action="would_purge",
                paths=[path.as_posix() for path in purge_paths],
                bytes_deleted=bytes_deleted,
                hash=current_hash,
                indexed=False,
                dry_run=True,
            )

        for path in purge_paths:
            resolve_corpus_target(self.root, path).unlink(missing_ok=True)

        indexed = False
        if self.index_root is not None and self.embedder is not None:
            reindex_paths(
                self.root,
                self.index_root,
                self.embedder,
                [path.as_posix() for path in purge_paths],
            )
            indexed = True

        return PurgeResp(
            path=target_rel.as_posix(),
            action="purged",
            paths=[path.as_posix() for path in purge_paths],
            bytes_deleted=bytes_deleted,
            hash=current_hash,
            indexed=indexed,
            dry_run=False,
        )

    def _collect_purge_paths(self, target_rel: Path, *, include_related_tombstone: bool) -> list[Path]:
        paths = [target_rel]
        if not include_related_tombstone or target_rel.name.endswith(".tombstone.md"):
            return paths
        tombstone_rel = target_rel.with_name(f"{target_rel.stem}.tombstone.md")
        tombstone_target = resolve_corpus_target(self.root, tombstone_rel)
        if tombstone_target.exists():
            paths.append(tombstone_rel)
        return paths


def _validate_target(target: str) -> Path:
    if target.startswith("/"):
        raise DoryValidationError("target must be relative to corpus root")
    if ".." in Path(target).parts:
        raise DoryValidationError("target cannot escape corpus root")
    if not _VALID_TARGET_PATTERN.match(target):
        raise DoryValidationError(f"invalid target path: {target}")
    return Path(target)


def _is_default_purge_path(path: Path) -> bool:
    normalized = path.as_posix()
    return normalized.startswith(_DEFAULT_PURGE_ROOTS)


def _is_protected(path: Path, text: str) -> bool:
    normalized = path.as_posix()
    if normalized.startswith(_PROTECTED_ROOTS):
        return True
    try:
        frontmatter = load_markdown_document(text).frontmatter
    except ValueError:
        return False
    return bool(frontmatter.get("canonical") is True)
