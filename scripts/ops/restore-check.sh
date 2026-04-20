#!/usr/bin/env bash
set -euo pipefail

RESTORE_ROOT="${1:-/tmp/dory-restore-check}"
INDEX_ROOT="${RESTORE_ROOT}/.index"

mkdir -p "${RESTORE_ROOT}"
uv run python -m dory_cli.main --corpus-root "${RESTORE_ROOT}" --index-root "${INDEX_ROOT}" reindex
uv run python -m dory_cli.main --corpus-root "${RESTORE_ROOT}" --index-root "${INDEX_ROOT}" status
