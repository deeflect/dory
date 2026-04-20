from __future__ import annotations

from pathlib import Path

from dory_cli.main import app
from dory_core.types import WriteReq
from dory_core.write import WriteEngine


def test_link_cli_commands(
    cli_runner,
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder).write(
        WriteReq(
            kind="append",
            target="knowledge/links.md",
            content="See [[people/alex|Alex]].",
            frontmatter={"title": "Links", "type": "knowledge"},
        )
    )

    common_args = ["--corpus-root", str(corpus_root), "--index-root", str(index_root)]

    neighbors = cli_runner.invoke(app, [*common_args, "neighbors", "knowledge/links.md"])
    backlinks = cli_runner.invoke(app, [*common_args, "backlinks", "people/alex.md"])
    lint = cli_runner.invoke(app, [*common_args, "lint"])

    assert neighbors.exit_code == 0
    assert "people/alex.md" in neighbors.stdout
    assert backlinks.exit_code == 0
    assert "knowledge/links.md" in backlinks.stdout
    assert lint.exit_code == 0
    assert '"count": 0' in lint.stdout
