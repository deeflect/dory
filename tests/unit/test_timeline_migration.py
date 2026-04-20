from __future__ import annotations

from pathlib import Path

from dory_core.timeline_migration import migrate_corpus, migrate_document


def test_project_state_gets_timeline_marker_once(tmp_path: Path) -> None:
    result = migrate_document(
        Path("projects/dory/state.md"),
        """---
title: Dory
type: project
status: active
---

Current truth.

- 2026-04-10: Added watcher support.
""",
    )

    assert result.changed is True
    assert result.rendered is not None
    assert "<!-- TIMELINE: append-only below this line -->" in result.rendered
    assert "Current truth." in result.rendered


def test_migration_is_idempotent(tmp_path: Path) -> None:
    original = """---
title: Dory
type: project
status: active
has_timeline: true
---

Current truth.

<!-- TIMELINE: append-only below this line -->

- 2026-04-10: Added watcher support.
"""
    once = migrate_document(Path("projects/dory/state.md"), original)
    twice = migrate_document(Path("projects/dory/state.md"), original if once.rendered is None else once.rendered)

    assert once.changed is False
    assert twice.changed is False


def test_migrate_corpus_writes_supported_targets(tmp_path: Path) -> None:
    target = tmp_path / "projects" / "dory" / "state.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """---
title: Dory
type: project
status: active
---

Current truth.
""",
        encoding="utf-8",
    )

    result = migrate_corpus(tmp_path, write=True)

    assert result.changed_paths == ("projects/dory/state.md",)
    assert "<!-- TIMELINE: append-only below this line -->" in target.read_text(encoding="utf-8")
