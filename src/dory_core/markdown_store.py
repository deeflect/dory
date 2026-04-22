from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path

from dory_core.chunking import Chunk, chunk_markdown
from dory_core.frontmatter import load_markdown_document


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    path: Path
    frontmatter: dict[str, object]
    content: str
    hash: str
    size: int
    mtime: str
    chunks: list[Chunk] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MarkdownScanResult:
    documents: list[MarkdownDocument] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)


class MarkdownStore:
    def walk(self, root: Path, *, exclude_prefixes: tuple[str, ...] = ()) -> list[MarkdownDocument]:
        return self.scan(root, exclude_prefixes=exclude_prefixes).documents

    def scan(self, root: Path, *, exclude_prefixes: tuple[str, ...] = ()) -> MarkdownScanResult:
        documents: list[MarkdownDocument] = []
        skipped_paths: list[str] = []
        if not root.exists():
            return MarkdownScanResult()

        for path in sorted(root.rglob("*.md")):
            relative_path = path.relative_to(root)
            if _is_excluded(relative_path, exclude_prefixes):
                continue
            text = path.read_text(encoding="utf-8")
            try:
                parsed = load_markdown_document(text)
            except ValueError:
                skipped_paths.append(str(relative_path))
                continue
            stat = path.stat()
            documents.append(
                MarkdownDocument(
                    path=relative_path,
                    frontmatter=parsed.frontmatter,
                    content=text,
                    hash=f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
                    size=len(text.encode("utf-8")),
                    mtime=str(int(stat.st_mtime)),
                    chunks=chunk_markdown(text),
                )
            )

        return MarkdownScanResult(documents=documents, skipped_paths=skipped_paths)


def _is_excluded(path: Path, prefixes: tuple[str, ...]) -> bool:
    normalized = path.as_posix()
    return any(normalized.startswith(prefix) for prefix in prefixes)
