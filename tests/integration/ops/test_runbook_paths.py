from __future__ import annotations

from pathlib import Path


def test_runbook_mentions_reindex_recovery() -> None:
    runbook = Path("references/runbook.md").read_text(encoding="utf-8")
    backup_script = Path("scripts/ops/backup.sh").read_text(encoding="utf-8")
    restore_script = Path("scripts/ops/restore-check.sh").read_text(encoding="utf-8")
    cron_script = Path("scripts/ops/install-backup-cron.sh").read_text(encoding="utf-8")

    assert "reindex" in runbook.lower()
    assert 'git -C "$CORPUS_ROOT" push' in backup_script
    assert "status" in restore_script
    assert "crontab" in cron_script
    assert "backup.sh" in cron_script
