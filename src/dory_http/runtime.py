from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dory_core.runtime import DoryRuntime


@dataclass(frozen=True, slots=True)
class HttpRuntime:
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path | None
    allow_no_auth: bool
    core: DoryRuntime
