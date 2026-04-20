#!/usr/bin/env bash
set -euo pipefail

CORPUS_ROOT="${DORY_CORPUS_ROOT:-/var/lib/dory}"

git -C "$CORPUS_ROOT" push
