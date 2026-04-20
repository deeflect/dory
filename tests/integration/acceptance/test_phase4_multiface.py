from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.types import SearchReq, WakeReq, WriteReq
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine
from dory_http.app import build_app
from dory_http.auth import issue_token
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
        return SearchEngine(self.index_root, self.fake_embedder).search(
            SearchReq.model_validate(req)
        )

    def wake(self, req: dict[str, object]):
        return WakeBuilder(self.corpus_root).build(WakeReq.model_validate(req))

    def link(self, req: dict[str, object]):
        del req
        return {"op": "neighbors", "edges": [], "count": 0}


def _load_provider_class():
    provider_path = Path("plugins/hermes-dory/provider.py")
    spec = importlib.util.spec_from_file_location("hermes_dory_provider", provider_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load provider module from {provider_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.DoryMemoryProvider


def test_phase4_all_faces_hit_same_memory_surface(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    assert multiface_round_trip(tmp_path, sample_corpus_root, fake_embedder) is True


def multiface_round_trip(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> bool:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    token = issue_token("multiface", auth_tokens_path)
    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))

    write_response = client.post(
        "/v1/write",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "append",
            "target": "core/active.md",
            "content": "Shared note via HTTP is visible through every face.",
        },
    )
    if write_response.status_code != 200:
        return False

    provider_cls = _load_provider_class()
    hermes = provider_cls(base_url=str(client.base_url), token=token, client=client)
    hermes_search = hermes.search("Shared note via HTTP", k=5)

    core = SharedCore(corpus_root=corpus_root, index_root=index_root, fake_embedder=fake_embedder)
    stdin = StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "dory_wake",
                    "arguments": {
                        "agent": "codex",
                        "budget_tokens": 600,
                    },
                },
            }
        )
        + "\n"
    )
    stdout = StringIO()
    serve_stdio(core, stdin=stdin, stdout=stdout)
    wake_payload = json.loads(json.loads(stdout.getvalue().splitlines()[0])["result"]["content"][0]["text"])

    status_response = client.get(
        "/v1/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    codex_wrapper = Path("scripts/codex/dory")
    openclaw_entry = Path("packages/openclaw-dory/src/index.ts")
    openclaw_manifest = Path("packages/openclaw-dory/openclaw.plugin.json")

    return all(
        [
            any(result["path"] == "core/active.md" for result in hermes_search["results"]),
            "Shared note via HTTP" in wake_payload["block"],
            status_response.status_code == 200,
            codex_wrapper.exists(),
            openclaw_entry.exists(),
            openclaw_manifest.exists(),
        ]
    )
