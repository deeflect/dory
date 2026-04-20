from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_maintain_wiki_health_reports_stale_and_missing_evidence(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "rooster.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Rooster
type: wiki
status: stale
canonical: true
source_kind: generated
temperature: warm
updated: 2026-03-01
---

# Rooster

## Summary
Rooster is the active focus this week.

## Key claims
- Rooster is the active focus this week. [confirmed, high, fresh]

## Evidence
- rooster-focus
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "maintain",
            "wiki-health",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["report_path"] == "inbox/maintenance/wiki-health.json"
    assert "wiki/projects/rooster.md" in payload["report"]["stale_pages"]


def test_ops_wiki_health_reports_low_confidence_items(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "concepts" / "crawstr.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Crawstr
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Crawstr

## Summary
Compiled summary.

## Key claims
- Crawstr maybe uses X API. [likely, low, stale]

## Evidence
- crawstr-claim
  - logs/daily/2026-04-10.md (1:1) [durable] Discussion notes

## Contradictions
- no contradiction confirmed.

## Open questions
- confirm API choice.
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-health",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "wiki/concepts/crawstr.md" in payload["report"]["low_confidence"]
    assert "wiki/concepts/crawstr.md" in payload["report"]["open_questions"]
    assert payload["report"]["contradictions"] == []


def test_ops_wiki_health_reports_event_mismatch(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "rooster.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Rooster
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Rooster

## Current State
- Rooster is active.

## Evidence
### Added
- sources/semantic/2026/04/14/rooster-write.md

## Timeline
- 2026-04-15T00:00:00Z: Retired: Rooster is active.
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-health",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["report"]["event_mismatch"] == ["wiki/projects/rooster.md"]


def test_ops_wiki_health_reports_state_conflict(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "rooster.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Rooster
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Rooster

## Current State
- Rooster is active.

## Evidence
### Retired
- sources/semantic/2026/04/14/rooster-forget.md

## Timeline
- 2026-04-15T00:00:00Z: Retired: Rooster is active.
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-health",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["report"]["state_conflict"] == ["wiki/projects/rooster.md"]


def test_ops_wiki_health_reports_claim_mismatch(cli_runner, tmp_path: Path) -> None:
    from dory_core.claim_store import ClaimStore

    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    store = ClaimStore(corpus_root / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is the active focus this week.",
        evidence_path="sources/semantic/2026/04/14/rooster-write.md",
    )
    wiki_path = corpus_root / "wiki" / "projects" / "rooster.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Rooster
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Rooster

## Current State
- Rooster is paused.

## Evidence
### Added
- sources/semantic/2026/04/14/rooster-write.md

## Timeline
- 2026-04-15T00:00:00Z: Rooster is paused.
""",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "ops",
            "wiki-health",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["report"]["claim_mismatch"] == ["wiki/projects/rooster.md"]
