from __future__ import annotations

from pathlib import Path

from dory_core.link import extract_known_entity_edges, extract_wikilinks, load_known_entities


def test_link_parser_extracts_wikilinks() -> None:
    edges = extract_wikilinks("Talked to [[people/alex|Alex]] about [[projects/dory|Dory]].")

    assert len(edges) == 2
    assert edges[0].to_path == "people/alex.md"


def test_link_parser_ignores_fenced_code_blocks() -> None:
    edges = extract_wikilinks(
        """Talked to [[people/alex|Alex]].

```bash
if [[ -f "$HOME/SOUL.md" ]]; then
  echo "ok"
fi
```
"""
    )

    assert len(edges) == 1
    assert edges[0].to_path == "people/alex.md"


def test_known_entity_edges_detect_plain_text_mentions(tmp_path: Path) -> None:
    people = tmp_path / "people" / "alex.md"
    people.parent.mkdir(parents=True, exist_ok=True)
    people.write_text("---\ntitle: Alex\ntype: person\nstatus: active\n---\n", encoding="utf-8")
    project = tmp_path / "projects" / "dory" / "state.md"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text("---\ntitle: Dory\ntype: project\nstatus: active\n---\n", encoding="utf-8")

    entities = load_known_entities(tmp_path)
    edges = extract_known_entity_edges(
        "Alex discussed Dory rollout work.",
        from_path="knowledge/meeting.md",
        known_entities=entities,
    )

    assert {edge.to_path for edge in edges} == {"people/alex.md", "projects/dory/state.md"}
