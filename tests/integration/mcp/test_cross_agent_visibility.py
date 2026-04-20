from __future__ import annotations

import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.types import SearchReq, WakeReq, WriteReq
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine
from dory_mcp.server import serve_stdio


@dataclass
class SharedCore:
    corpus_root: Path
    index_root: Path
    fake_embedder: object

    def write(self, req: dict[str, object]):
        return WriteEngine(
            root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.fake_embedder,
        ).write(WriteReq.model_validate(req))

    def get(self, req: dict[str, object]):
        path = self.corpus_root / str(req["path"])
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start_line = int(req.get("from", 1))
        limit = req.get("lines")
        start_index = max(start_line - 1, 0)
        end_index = len(lines) if limit is None else start_index + int(limit)
        return {
            "path": str(req["path"]),
            "content": "\n".join(lines[start_index:end_index]),
        }

    def search(self, req: dict[str, object]):
        return SearchEngine(self.index_root, self.fake_embedder).search(SearchReq.model_validate(req))

    def wake(self, req: dict[str, object]):
        return WakeBuilder(self.corpus_root).build(WakeReq.model_validate(req))

    def link(self, req: dict[str, object]):
        return {"op": req.get("op"), "edges": []}


def test_write_from_agent_a_appears_in_agent_b_next_wake(
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

    reindex_corpus(corpus_root, index_root, fake_embedder)
    core = SharedCore(corpus_root=corpus_root, index_root=index_root, fake_embedder=fake_embedder)
    stdin = StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_write",
                            "arguments": {
                                "kind": "append",
                                "target": "core/active.md",
                                "content": "Palette sync from Claude Code should be visible to Codex next session.",
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_get",
                            "arguments": {"path": "core/active.md", "from": 1, "lines": 200},
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_search",
                            "arguments": {"query": "Palette sync from Claude Code", "k": 5},
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "dory_wake", "arguments": {"agent": "codex", "budget_tokens": 600}},
                    }
                ),
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(core, stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    get_payload = json.loads(responses[1]["result"]["content"][0]["text"])
    search_payload = json.loads(responses[2]["result"]["content"][0]["text"])
    wake_payload = json.loads(responses[3]["result"]["content"][0]["text"])

    assert "Palette sync from Claude Code" in get_payload["content"]
    assert any(result["path"] == "core/active.md" for result in search_payload["results"])
    assert "Palette sync from Claude Code" in wake_payload["block"]
