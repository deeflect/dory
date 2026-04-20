from __future__ import annotations

import json
from pathlib import Path

from dory_core.corpus_normalization import (
    build_extracted_decision,
    parse_headless_json_response,
    parse_knowledge_classifications,
    parse_project_classifications,
)


def test_build_extracted_decision_from_decision_section() -> None:
    result = build_extracted_decision(
        Path("logs/daily/2026-02-10-digest.md"),
        {
            "title": "Tue (02/10) — LangExtract, CCC qmd query fix",
            "created": "2026-02-10",
            "type": "daily",
        },
        """# Daily Digest

## 📋 Decisions & Outcomes

- Switched from `qmd query` to `qmd search`
- Installed private mesh VPN on the local workstation

## ✅ Tasks

- Did other work
""",
    )

    assert result is not None
    assert result.target_rel == Path("decisions/extracted/2026-02-10-daily-2026-02-10-digest.md")
    assert result.frontmatter["type"] == "decision"
    assert result.frontmatter["source_kind"] == "extracted"
    assert "## 📋 Decisions & Outcomes" in result.body
    assert "qmd search" in result.body
    assert "## ✅ Tasks" not in result.body


def test_build_extracted_decision_from_marker_lines() -> None:
    result = build_extracted_decision(
        Path("logs/sessions/borb/raw/2026-03-06-blog-backfill.md"),
        {
            "title": "Blog backfill",
            "created": "2026-03-06",
            "type": "session",
        },
        """# Notes

- [DECISION] Use the mascot cover
- [DECISION] Backfill only high-signal posts
""",
    )

    assert result is not None
    assert "Extracted [DECISION] lines" in result.body
    assert "mascot cover" in result.body


def test_parse_headless_json_response_unwraps_cli_wrapper() -> None:
    payload = {
        "session_id": "123",
        "response": json.dumps(
            [
                {
                    "source_rel": "projects/foo.md",
                    "action": "leave_for_review",
                    "target_slug": None,
                    "knowledge_area": None,
                    "confidence": 0.2,
                    "reason": "ambiguous",
                }
            ]
        ),
        "stats": {},
    }
    parsed = parse_headless_json_response(json.dumps(payload))
    assert isinstance(parsed, list)
    assert parsed[0]["source_rel"] == "projects/foo.md"


def test_parse_project_classifications_validates_fields() -> None:
    payload = {
        "session_id": "123",
        "response": json.dumps(
            [
                {
                    "source_rel": "projects/ccc-architecture.md",
                    "action": "project_support",
                    "target_slug": "content-command-center",
                    "knowledge_area": None,
                    "confidence": 0.93,
                    "reason": "Clearly a support doc for CCC.",
                }
            ]
        ),
        "stats": {},
    }
    parsed = parse_project_classifications(json.dumps(payload))
    assert parsed[0].target_slug == "content-command-center"
    assert parsed[0].confidence == 0.93


def test_parse_knowledge_classifications_validates_fields() -> None:
    payload = {
        "session_id": "123",
        "response": json.dumps(
            [
                {
                    "source_rel": "knowledge/clawsy-ai-plan.md",
                    "action": "project_state",
                    "target_slug": "clawsy",
                    "knowledge_area": None,
                    "confidence": 0.88,
                    "reason": "The doc is a project plan for one initiative.",
                }
            ]
        ),
        "stats": {},
    }
    parsed = parse_knowledge_classifications(json.dumps(payload))
    assert parsed[0].action == "project_state"
    assert parsed[0].target_slug == "clawsy"
