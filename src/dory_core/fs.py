from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dory_core.errors import DoryValidationError


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)

    os.replace(temp_path, path)


def resolve_corpus_target(root: Path, relative_path: Path) -> Path:
    root_resolved = root.resolve()
    target = root / relative_path
    parent_resolved = target.parent.resolve(strict=False)
    try:
        parent_resolved.relative_to(root_resolved)
    except ValueError as err:
        raise DoryValidationError("target cannot escape corpus root") from err
    return parent_resolved / target.name
