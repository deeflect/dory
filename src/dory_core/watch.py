from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from dory_core.embedding import ContentEmbedder
from dory_core.index.reindex import ReindexResult, reindex_paths


class MarkdownChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        root: Path,
        index_root: Path,
        embedder: ContentEmbedder,
    ) -> None:
        FileSystemEventHandler.__init__(self)
        self.root = Path(root)
        self.index_root = Path(index_root)
        self.embedder = embedder
        self.last_result: ReindexResult | None = None

    def on_modified(self, event: FileSystemEvent) -> ReindexResult | None:
        return self._reindex_event_paths(event)

    def on_created(self, event: FileSystemEvent) -> ReindexResult | None:
        return self._reindex_event_paths(event)

    def on_deleted(self, event: FileSystemEvent) -> ReindexResult | None:
        return self._reindex_event_paths(event)

    def on_moved(self, event: FileSystemEvent) -> ReindexResult | None:
        paths = [event.src_path]
        dest_path = getattr(event, "dest_path", "")
        if dest_path:
            paths.append(dest_path)
        return self._reindex_paths(paths, is_directory=event.is_directory)

    def _reindex_event_paths(self, event: FileSystemEvent) -> ReindexResult | None:
        return self._reindex_paths([event.src_path], is_directory=event.is_directory)

    def _reindex_paths(self, paths: list[str], *, is_directory: bool) -> ReindexResult | None:
        if is_directory:
            return None

        relative_paths = relative_markdown_paths(paths, root=self.root)
        if not relative_paths:
            return None

        result = reindex_paths(self.root, self.index_root, self.embedder, relative_paths)
        self.last_result = result
        return result


@dataclass(slots=True)
class WatchCoalescer:
    debounce_seconds: float = 1.0
    pending_paths: set[str] = field(default_factory=set)
    last_event_at: float | None = None

    def record(self, path: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        self.pending_paths.add(path)
        self.last_event_at = timestamp
        return False

    def ready(self, *, now: float | None = None) -> bool:
        if self.last_event_at is None or not self.pending_paths:
            return False
        timestamp = time.monotonic() if now is None else now
        return timestamp - self.last_event_at >= self.debounce_seconds

    def drain(self) -> list[str]:
        drained = sorted(self.pending_paths)
        self.pending_paths.clear()
        self.last_event_at = None
        return drained


class BufferedMarkdownChangeHandler(FileSystemEventHandler):
    def __init__(self, coalescer: WatchCoalescer) -> None:
        FileSystemEventHandler.__init__(self)
        self.coalescer = coalescer

    def on_modified(self, event: FileSystemEvent) -> None:
        self._record(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._record(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._record(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        paths = [event.src_path]
        dest_path = getattr(event, "dest_path", "")
        if dest_path:
            paths.append(dest_path)
        for relative_path in relative_markdown_paths(paths):
            self.coalescer.record(relative_path)

    def _record(self, event: FileSystemEvent) -> None:
        if not is_markdown_change(event):
            return
        self.coalescer.record(str(Path(event.src_path)))


def is_markdown_change(event: FileSystemEvent) -> bool:
    if event.is_directory:
        return False
    return Path(event.src_path).suffix.lower() == ".md"


def relative_markdown_paths(paths: list[str], *, root: Path | None = None) -> list[str]:
    relative_paths: list[str] = []
    resolved_root = root.resolve() if root is not None else None
    for raw_path in paths:
        path = Path(raw_path)
        if path.suffix.lower() != ".md":
            continue
        if resolved_root is None:
            relative_paths.append(str(path))
            continue
        try:
            relative_paths.append(str(path.resolve().relative_to(resolved_root)))
        except ValueError:
            continue
    return relative_paths


def is_session_markdown(path: Path, *, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return relative.parts[:2] == ("logs", "sessions") and relative.suffix.lower() == ".md"
