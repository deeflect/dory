from __future__ import annotations

import pytest

from dory_core.frontmatter import dump_markdown_document, load_markdown_document


def test_frontmatter_parses_required_fields() -> None:
    doc = load_markdown_document(
        "---\n"
        "title: User\n"
        "created: 2026-04-07\n"
        "type: core\n"
        "status: active\n"
        "---\n"
        "\n"
        "Hello world.\n"
    )

    assert doc.frontmatter["title"] == "User"
    assert doc.body == "Hello world.\n"


def test_frontmatter_requires_fenced_header() -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        load_markdown_document("Hello world.\n")


def test_frontmatter_parses_multiline_yaml_lists() -> None:
    doc = load_markdown_document(
        "---\n"
        "title: Weekly Digest\n"
        "priorities:\n"
        "  - shipping\n"
        "  - recovery\n"
        "---\n"
        "\n"
        "Body.\n"
    )

    assert doc.frontmatter["priorities"] == ["shipping", "recovery"]


def test_frontmatter_normalizes_yaml_dates_and_tolerates_legacy_scalars() -> None:
    doc = load_markdown_document(
        "---\n"
        'title: "Operation: Casey Everywhere" — Authority Building Master Plan v4\n'
        "created: 2026-04-07\n"
        "---\n"
        "\n"
        "Body.\n"
    )

    assert doc.frontmatter["title"] == '"Operation: Casey Everywhere" — Authority Building Master Plan v4'
    assert doc.frontmatter["created"] == "2026-04-07"


def test_frontmatter_dump_emits_strict_yaml() -> None:
    rendered = dump_markdown_document(
        {
            "title": "Idea: Claude API Policy Clarity Tool",
            "tags": ["alpha", "beta"],
            "canonical": False,
        },
        "Body.\n",
    )

    doc = load_markdown_document(rendered)

    assert doc.frontmatter["title"] == "Idea: Claude API Policy Clarity Tool"
    assert doc.frontmatter["tags"] == ["alpha", "beta"]
    assert "title: 'Idea: Claude API Policy Clarity Tool'" in rendered
