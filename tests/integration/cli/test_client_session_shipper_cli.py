from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_client_session_shipper_auto_discovers_claude_session(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude" / "projects" / "palace"
    claude_root.mkdir(parents=True)
    (claude_root / "abc123.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-04-12T10:00:00Z",
                        "sessionId": "abc123",
                        "message": {"role": "user", "content": "remember rooster is active"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-04-12T10:01:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Rooster is the active focus this week."}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spool_root = tmp_path / "spool"
    checkpoints_path = tmp_path / "checkpoints.json"
    env = {
        **os.environ,
        "DORY_CLAUDE_PROJECTS_ROOT": str(claude_root.parent.parent),
    }

    result = subprocess.run(
        [
            "python3",
            "scripts/ops/client-session-shipper.py",
            "--harnesses",
            "claude",
            "--device",
            "macbook",
            "--spool-root",
            str(spool_root),
            "--checkpoints-path",
            str(checkpoints_path),
            "--base-url",
            "http://127.0.0.1:8766",
            "--no-flush",
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["captures"]) == 1
    assert payload["captures"][0]["path"] == "logs/sessions/claude/macbook/2026-04-12-abc123.md"
    pending = sorted(spool_root.glob("*.json"))
    assert len(pending) == 1
    queued = json.loads(pending[0].read_text(encoding="utf-8"))
    assert "Rooster is the active focus this week." in queued["content"]
    assert checkpoints_path.exists()
