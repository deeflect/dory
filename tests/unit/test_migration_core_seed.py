from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.migration_core_seed import seed_core_from_root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_seed_copies_only_dory_standard_core_files(tmp_path: Path) -> None:
    source = tmp_path / "brain"
    corpus = tmp_path / "corpus"
    _write(source / "SOUL.md", "# SOUL.md — Voice\n\nSharp and direct.\n")
    _write(source / "USER.md", "---\ntitle: Casey\n---\n\nIdentity notes.\n")
    _write(source / "IDENTITY.md", "# IDENTITY\n\nprofessional positioning\n")
    _write(source / "ENV.md", "# ENV\n\nmachines and services\n")
    _write(source / "ACTIVE.md", "# ACTIVE\n\ncurrent focus\n")
    _write(source / "DEFAULTS.md", "# DEFAULTS\n\nmodel and tool defaults\n")
    # Non-Dory core brain files that must NOT land in core/:
    _write(source / "AGENTS.md", "# Agents\n\nagent protocol\n")
    _write(source / "TOOLS.md", "# Tools\n\nLocal cheatsheet.\n")
    _write(source / "MEMORY.md", "# Memory\n\nfiling rules\n")
    _write(source / "DREAMS.md", "# Dreams\n\ndream cycle\n")
    _write(source / "HEARTBEAT.md", "# Heartbeat\n\noperational snapshot\n")
    _write(source / "lowercase-not-core.md", "# skip me\n")

    result = seed_core_from_root(source, corpus)

    copied_names = sorted(Path(p).name for p in result.copied)
    assert copied_names == [
        "active.md",
        "defaults.md",
        "env.md",
        "identity.md",
        "soul.md",
        "user.md",
    ]
    for stem in ("agents", "tools", "memory", "dreams", "heartbeat"):
        assert not (corpus / "core" / f"{stem}.md").exists()


def test_seed_synthesizes_frontmatter_for_bare_files(tmp_path: Path) -> None:
    source = tmp_path / "brain"
    corpus = tmp_path / "corpus"
    _write(source / "SOUL.md", "# SOUL — rules\n\nbody\n")

    seed_core_from_root(source, corpus)

    doc = load_markdown_document((corpus / "core" / "soul.md").read_text(encoding="utf-8"))
    assert doc.frontmatter["type"] == "core"
    assert doc.frontmatter["status"] == "active"
    assert doc.frontmatter["canonical"] is True
    assert doc.frontmatter["source_kind"] == "canonical"
    assert doc.frontmatter["temperature"] == "hot"
    assert "body" in doc.body


def test_seed_preserves_existing_title(tmp_path: Path) -> None:
    source = tmp_path / "brain"
    corpus = tmp_path / "corpus"
    _write(source / "USER.md", "---\ntitle: My Custom Title\n---\n\nbody\n")

    seed_core_from_root(source, corpus)

    doc = load_markdown_document((corpus / "core" / "user.md").read_text(encoding="utf-8"))
    assert doc.frontmatter["title"] == "My Custom Title"


def test_seed_infers_title_from_h1_when_no_frontmatter(tmp_path: Path) -> None:
    source = tmp_path / "brain"
    corpus = tmp_path / "corpus"
    _write(source / "IDENTITY.md", "# Borb Identity\n\nbody\n")

    seed_core_from_root(source, corpus)

    doc = load_markdown_document((corpus / "core" / "identity.md").read_text(encoding="utf-8"))
    assert doc.frontmatter["title"] == "Borb Identity"


def test_seed_dry_run_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "brain"
    corpus = tmp_path / "corpus"
    _write(source / "SOUL.md", "body\n")

    result = seed_core_from_root(source, corpus, dry_run=True)

    assert result.copied == ["core/soul.md"]
    assert not (corpus / "core").exists()


def test_seed_handles_missing_source(tmp_path: Path) -> None:
    result = seed_core_from_root(tmp_path / "nope", tmp_path / "corpus")

    assert result.copied == []
