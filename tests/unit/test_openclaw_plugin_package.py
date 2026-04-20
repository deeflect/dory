from __future__ import annotations

import json
from pathlib import Path


def test_openclaw_plugin_manifest_declares_memory_slot() -> None:
    manifest_path = Path("packages/openclaw-dory/openclaw.plugin.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["id"] == "dory-memory"
    assert payload["kind"] == "memory"
    assert payload["configSchema"]["required"] == ["baseUrl"]
    assert "tokenEnv" in payload["configSchema"]["properties"]
    assert "entry" not in payload
    assert (manifest_path.parent / "dist" / "index.js").exists()


def test_openclaw_plugin_package_declares_sdk_entrypoint() -> None:
    package_path = Path("packages/openclaw-dory/package.json")
    payload = json.loads(package_path.read_text(encoding="utf-8"))

    assert payload["name"] == "dory-memory"
    assert payload["openclaw"]["extensions"] == ["./dist/index.js"]


def test_openclaw_plugin_source_exports_sdk_registration_contract() -> None:
    source = Path("packages/openclaw-dory/src/index.ts").read_text(encoding="utf-8")

    assert 'from "openclaw/plugin-sdk/plugin-entry"' in source
    assert "definePluginEntry({" in source
    assert "registerMemoryCapability" in source
    assert "class DoryMemorySearchManager" in source
    assert "promptBuilder:" in source
    assert 'kind: "memory"' in source
    assert 'name: "memory_write"' in source
    assert "/v1/memory-write" in source
    assert "/v1/recall-event" in source
    assert "/v1/active-memory" in source
    assert "/v1/public-artifacts" in source
    assert "tokenEnv" in source
    assert "subject" in source
    assert "confidence" in source
    assert "source" in source
    assert "dry_run" in source
    assert "force_inbox" in source
    assert "allow_canonical" in source
    assert "flushPlanResolver:" in source
    assert 'relativePath: "openclaw/compaction-flush.md"' in source
