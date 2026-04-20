from __future__ import annotations

import json
from pathlib import Path

from dory_http.auth import issue_token


def test_issue_token_persists_named_token(tmp_path: Path) -> None:
    path = tmp_path / "auth-tokens.json"

    token = issue_token("codex", path)

    assert token.startswith("dory_")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"codex": token}
