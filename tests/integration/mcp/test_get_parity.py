from __future__ import annotations

from pathlib import Path

import pytest

from dory_mcp.server import RuntimeCore, _render_result


def test_mcp_get_returns_http_parity_fields(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "user.md").write_text(
        "---\ntitle: User\ntype: core\n---\n# User\n\nHello world.\n",
        encoding="utf-8",
    )

    core = RuntimeCore(corpus_root=corpus_root, index_root=index_root, embedder=fake_embedder)
    default_payload = core.get({"path": "core/user.md", "from": 1, "lines": 7})
    payload = core.get({"path": "core/user.md", "from": 1, "lines": 7, "debug": True})

    assert default_payload["path"] == "core/user.md"
    assert default_payload["from"] == 1
    assert "lines_returned" not in default_payload
    assert "frontmatter" not in default_payload
    assert "hash" not in default_payload

    assert payload["path"] == "core/user.md"
    assert payload["from"] == 1
    assert payload["lines_returned"] == 7
    assert payload["total_lines"] >= 4
    assert payload["frontmatter"]["title"] == "User"
    assert payload["hash"].startswith("sha256:")
    assert "Hello world." in payload["content"]


def test_mcp_get_accepts_legacy_from_line_alias(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "user.md").write_text(
        "---\ntitle: User\ntype: core\n---\n# User\n\nHello world.\n",
        encoding="utf-8",
    )

    core = RuntimeCore(corpus_root=corpus_root, index_root=index_root, embedder=fake_embedder)
    payload = core.get({"path": "core/user.md", "from_line": 2, "lines": 1})

    assert payload["from"] == 2


def test_mcp_get_rejects_non_positive_line_limit(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "user.md").write_text("# User\n\nHello world.\n", encoding="utf-8")

    core = RuntimeCore(corpus_root=corpus_root, index_root=index_root, embedder=fake_embedder)

    with pytest.raises(ValueError, match="'lines' must be >= 1"):
        core.get({"path": "core/user.md", "from": 1, "lines": 0})


def test_mcp_status_is_json_renderable(tmp_path: Path, fake_embedder) -> None:
    core = RuntimeCore(corpus_root=tmp_path / "corpus", index_root=tmp_path / "index", embedder=fake_embedder)

    rendered = _render_result(core.status({"debug": True}))

    assert '"openclaw"' in rendered
