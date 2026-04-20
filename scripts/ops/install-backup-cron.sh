#!/usr/bin/env bash
set -euo pipefail

SCHEDULE="${1:-17 3 * * *}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SCRIPT="${SCRIPT_DIR}/backup.sh"
LOG_PATH="${DORY_BACKUP_LOG:-/var/log/dory-backup.log}"
TMP_CRON="$(mktemp)"
trap 'rm -f "${TMP_CRON}"' EXIT

EXISTING_CRON="$(crontab -l 2>/dev/null || true)"
printf '%s\n' "${EXISTING_CRON}" | grep -vF "${BACKUP_SCRIPT}" > "${TMP_CRON}" || true
printf '%s %s >> %s 2>&1\n' "${SCHEDULE}" "${BACKUP_SCRIPT}" "${LOG_PATH}" >> "${TMP_CRON}"
crontab "${TMP_CRON}"

printf 'Installed Dory backup cron: %s -> %s\n' "${SCHEDULE}" "${BACKUP_SCRIPT}"
