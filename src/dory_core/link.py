from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable

from dory_core.frontmatter import load_markdown_document

_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_FENCED_CODE_BLOCK_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_GENERIC_ENTITY_NAMES = {"active", "brief", "env", "index", "note", "notes", "soul", "state", "user"}


@dataclass(frozen=True, slots=True)
class LinkEdge:
    from_path: str
    to_path: str
    anchor: str
    created: str = ""


@dataclass(frozen=True, slots=True)
class KnownEntity:
    target_path: str
    aliases: tuple[str, ...]


def extract_wikilinks(
    markdown: str,
    *,
    from_path: str = "",
    created: str = "",
) -> list[LinkEdge]:
    markdown = _FENCED_CODE_BLOCK_PATTERN.sub("", markdown)
    edges: list[LinkEdge] = []
    for match in _WIKILINK_PATTERN.finditer(markdown):
        target, display = match.groups()
        edges.append(
            LinkEdge(
                from_path=from_path,
                to_path=target if target.endswith(".md") else f"{target}.md",
                anchor=display or target,
                created=created,
            )
        )
    return edges


def sync_document_edges(
    db_path: Path,
    *,
    from_path: str,
    markdown: str,
    created: str = "",
    known_entities: Iterable[KnownEntity] | None = None,
) -> int:
    edges = extract_wikilinks(markdown, from_path=from_path, created=created)
    if known_entities is not None:
        edges.extend(
            extract_known_entity_edges(
                markdown,
                from_path=from_path,
                created=created,
                known_entities=known_entities,
            )
        )
    deduped_edges = _dedupe_edges(edges)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM edges WHERE from_path = ?", (from_path,))
        connection.executemany(
            """
            INSERT INTO edges(from_path, to_path, anchor, created)
            VALUES (:from_path, :to_path, :anchor, :created)
            """,
            [asdict(edge) for edge in deduped_edges],
        )
        connection.commit()
    return len(deduped_edges)


class LinkService:
    def __init__(self, corpus_root: Path, index_root: Path) -> None:
        self.corpus_root = Path(corpus_root)
        self.db_path = Path(index_root) / "dory.db"

    def neighbors(self, path: str, direction: str = "out", depth: int = 1) -> dict[str, object]:
        if depth < 1:
            depth = 1
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            edges = self._collect_neighbors(connection, path=path, direction=direction, depth=depth)
        return {"op": "neighbors", "path": path, "edges": edges, "count": len(edges)}

    def _collect_neighbors(
        self,
        connection: sqlite3.Connection,
        *,
        path: str,
        direction: str,
        depth: int,
    ) -> list[dict[str, str]]:
        seen_nodes = {path}
        frontier = [path]
        collected: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()

        for _ in range(depth):
            next_frontier: list[str] = []
            for current in frontier:
                rows = self._load_edges(connection, current, direction)
                for row in rows:
                    edge_key = (row["from"], row["to"], row["anchor"])
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)
                    collected.append(row)
                    neighbor = row["to"] if row["from"] == current else row["from"]
                    if neighbor not in seen_nodes:
                        seen_nodes.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        return collected

    def _load_edges(
        self,
        connection: sqlite3.Connection,
        path: str,
        direction: str,
    ) -> list[dict[str, str]]:
        if direction == "in":
            rows = connection.execute(
                "SELECT from_path, to_path, anchor, created FROM edges WHERE to_path = ?",
                (path,),
            ).fetchall()
            return [
                {
                    "from": row["from_path"],
                    "to": row["to_path"],
                    "anchor": row["anchor"],
                    "created": row["created"],
                }
                for row in rows
            ]
        if direction == "both":
            out_rows = connection.execute(
                "SELECT from_path, to_path, anchor, created FROM edges WHERE from_path = ?",
                (path,),
            ).fetchall()
            in_rows = connection.execute(
                "SELECT from_path, to_path, anchor, created FROM edges WHERE to_path = ?",
                (path,),
            ).fetchall()
            return [
                {
                    "from": row["from_path"],
                    "to": row["to_path"],
                    "anchor": row["anchor"],
                    "created": row["created"],
                }
                for row in [*out_rows, *in_rows]
            ]
        rows = connection.execute(
            "SELECT from_path, to_path, anchor, created FROM edges WHERE from_path = ?",
            (path,),
        ).fetchall()
        return [
            {
                "from": row["from_path"],
                "to": row["to_path"],
                "anchor": row["anchor"],
                "created": row["created"],
            }
            for row in rows
        ]

    def backlinks(self, path: str) -> dict[str, object]:
        result = self.neighbors(path, direction="in")
        result["op"] = "backlinks"
        return result

    def lint(self) -> dict[str, object]:
        broken: list[dict[str, str]] = []
        self_links: list[dict[str, str]] = []
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT from_path, to_path, anchor, created FROM edges ORDER BY from_path, to_path"
            ).fetchall()

        for row in rows:
            from_path = row["from_path"]
            to_path = row["to_path"]
            if from_path == to_path:
                self_links.append({"from": from_path, "to": to_path})
                continue
            if not (self.corpus_root / to_path).exists():
                broken.append(
                    {
                        "from": from_path,
                        "to": to_path,
                        "reason": "target file does not exist",
                    }
                )

        return {"op": "lint", "broken": broken, "self_links": self_links, "count": len(broken)}


def extract_known_entity_edges(
    markdown: str,
    *,
    from_path: str,
    known_entities: Iterable[KnownEntity],
    created: str = "",
) -> list[LinkEdge]:
    normalized_text = f" {_normalize_entity_text(_FENCED_CODE_BLOCK_PATTERN.sub('', markdown))} "
    edges: list[LinkEdge] = []
    for entity in known_entities:
        if entity.target_path == from_path:
            continue
        matched_alias = next(
            (
                alias
                for alias in entity.aliases
                if alias and f" {alias} " in normalized_text
            ),
            None,
        )
        if matched_alias is None:
            continue
        edges.append(
            LinkEdge(
                from_path=from_path,
                to_path=entity.target_path,
                anchor=matched_alias,
                created=created,
            )
        )
    return edges


def load_known_entities(corpus_root: Path) -> list[KnownEntity]:
    root = Path(corpus_root)
    entities: list[KnownEntity] = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if relative.parts[:1] == (".git",):
            continue
        entity = _entity_from_path(relative)
        if entity is not None:
            entities.append(entity)
    return entities


def _entity_from_path(path: Path) -> KnownEntity | None:
    parts = path.parts
    if not parts:
        return None
    top = parts[0]
    if top == "people" and len(parts) == 2:
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, path.stem))
    if top == "projects" and len(parts) >= 3 and parts[2] == "state.md":
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, parts[1]))
    if top == "concepts" and len(parts) == 2:
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, path.stem))
    if top == "decisions" and len(parts) == 2:
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, path.stem))
    if top == "core" and len(parts) == 2:
        if path.stem in _GENERIC_ENTITY_NAMES:
            return None
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, path.stem))
    if top == "knowledge":
        stem = path.stem
        if stem in _GENERIC_ENTITY_NAMES:
            return None
        return KnownEntity(target_path=str(path), aliases=_aliases_for_path(path, stem))
    return None


def _aliases_for_path(path: Path, fallback: str) -> tuple[str, ...]:
    aliases = set(_build_aliases(fallback))
    try:
        document = load_markdown_document(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return tuple(sorted(aliases))

    title = document.frontmatter.get("title")
    if isinstance(title, str):
        aliases.update(_build_aliases(title))
    raw_aliases = document.frontmatter.get("aliases")
    if isinstance(raw_aliases, str):
        aliases.update(_build_aliases(raw_aliases))
    elif isinstance(raw_aliases, list):
        for alias in raw_aliases:
            if isinstance(alias, str):
                aliases.update(_build_aliases(alias))
    return tuple(sorted(alias for alias in aliases if alias))


def _build_aliases(raw: str) -> tuple[str, ...]:
    values = {
        _normalize_entity_text(raw),
        _normalize_entity_text(raw.replace("-", " ")),
        _normalize_entity_text(raw.replace("_", " ")),
    }
    return tuple(sorted(value for value in values if len(value) >= 3))


def _normalize_entity_text(text: str) -> str:
    return " ".join(part for part in _NON_ALNUM_RE.sub(" ", text.lower()).split() if part)


def _dedupe_edges(edges: Iterable[LinkEdge]) -> list[LinkEdge]:
    deduped: list[LinkEdge] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        key = (edge.from_path, edge.to_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped
