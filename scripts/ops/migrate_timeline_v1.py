#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from dory_core.timeline_migration import migrate_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conservatively add compiled-truth/timeline structure.")
    parser.add_argument("--corpus-root", type=Path, required=True, help="Path to the Dory corpus")
    parser.add_argument("--write", action="store_true", help="Persist migrated files")
    args = parser.parse_args(argv)

    result = migrate_corpus(args.corpus_root, write=args.write)
    print(result.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
