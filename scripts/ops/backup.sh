#!/usr/bin/env bash
set -euo pipefail

CORPUS_ROOT="${DORY_CORPUS_ROOT:-/var/lib/dory}"
COMMIT_MESSAGE_PREFIX="${DORY_BACKUP_COMMIT_PREFIX:-chore: backup dory corpus}"

if [[ ! -d "${CORPUS_ROOT}" ]]; then
  printf 'Dory corpus root does not exist: %s\n' "${CORPUS_ROOT}" >&2
  exit 1
fi

if ! git -C "${CORPUS_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'Dory corpus root is not a git repository: %s\n' "${CORPUS_ROOT}" >&2
  exit 1
fi

if ! git -C "${CORPUS_ROOT}" remote get-url origin >/dev/null 2>&1; then
  printf 'Dory corpus repository has no origin remote: %s\n' "${CORPUS_ROOT}" >&2
  exit 1
fi

if [[ -f "${CORPUS_ROOT}/.gitattributes" ]] && grep -q 'filter=git-crypt' "${CORPUS_ROOT}/.gitattributes"; then
  if ! command -v git-crypt >/dev/null 2>&1; then
    printf 'Dory corpus uses git-crypt, but git-crypt is not installed on this host: %s\n' "${CORPUS_ROOT}" >&2
    exit 1
  fi
fi

git -C "${CORPUS_ROOT}" add -A -- . \
  ':!.dory' \
  ':!.index' \
  ':!.env' \
  ':!.env.*' \
  ':!*.key' \
  ':!*.log' \
  ':!.DS_Store'

if ! git -C "${CORPUS_ROOT}" diff --cached --quiet; then
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git -C "${CORPUS_ROOT}" commit -m "${COMMIT_MESSAGE_PREFIX} ${timestamp}"
else
  printf 'No Dory corpus changes to commit: %s\n' "${CORPUS_ROOT}"
fi

branch="$(git -C "${CORPUS_ROOT}" branch --show-current)"
if [[ -z "${branch}" ]]; then
  printf 'Dory corpus repository is not on a branch: %s\n' "${CORPUS_ROOT}" >&2
  exit 1
fi

git -C "${CORPUS_ROOT}" push -u origin "HEAD:${branch}"
