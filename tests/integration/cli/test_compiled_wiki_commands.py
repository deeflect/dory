from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_maintain_wiki_health_reports_stale_and_missing_evidence(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "sample.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Sample
type: wiki
status: stale
canonical: true
source_kind: generated
temperature: warm
updated: 2026-03-01
---

# Sample

## Summary
Sample is the active focus this week.

## Key claims
- Sample is the active focus this week. [confirmed, high, fresh]

## Evidence
- sample-focus
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
    assert "wiki/projects/sample.md" in payload["report"]["stale_pages"]


def test_ops_wiki_health_reports_low_confidence_items(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "concepts" / "trawler.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Trawler
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Trawler

## Summary
Compiled summary.

## Key claims
- Trawler maybe uses X API. [likely, low, stale]

## Evidence
- trawler-claim
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
    assert "wiki/concepts/trawler.md" in payload["report"]["low_confidence"]
    assert "wiki/concepts/trawler.md" in payload["report"]["open_questions"]
    assert payload["report"]["contradictions"] == []


def test_ops_wiki_health_reports_event_mismatch(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "sample.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Sample
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Sample

## Current State
- Sample is active.

## Evidence
### Added
- sources/semantic/2026/04/14/sample-write.md

## Timeline
- 2026-04-15T00:00:00Z: Retired: Sample is active.
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
    assert payload["report"]["event_mismatch"] == ["wiki/projects/sample.md"]


def test_ops_wiki_health_reports_state_conflict(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    wiki_path = corpus_root / "wiki" / "projects" / "sample.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Sample
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Sample

## Current State
- Sample is active.

## Evidence
### Retired
- sources/semantic/2026/04/14/sample-forget.md

## Timeline
- 2026-04-15T00:00:00Z: Retired: Sample is active.
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
    assert payload["report"]["state_conflict"] == ["wiki/projects/sample.md"]


def test_ops_wiki_health_reports_claim_mismatch(cli_runner, tmp_path: Path) -> None:
    from dory_core.claim_store import ClaimStore

    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    store = ClaimStore(corpus_root / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:sample",
        kind="state",
        statement="Sample is the active focus this week.",
        evidence_path="sources/semantic/2026/04/14/sample-write.md",
    )
    wiki_path = corpus_root / "wiki" / "projects" / "sample.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Sample
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Sample

## Current State
- Sample is paused.

## Evidence
### Added
- sources/semantic/2026/04/14/sample-write.md

## Timeline
- 2026-04-15T00:00:00Z: Sample is paused.
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
    assert payload["report"]["claim_mismatch"] == ["wiki/projects/sample.md"]
