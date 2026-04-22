import json
from pathlib import Path

from dory_core.session_shipper import (
    SessionShipJob,
    SessionShipper,
    SessionSpool,
    SessionTransportResponse,
    build_default_shipper,
)


class FakeTransport:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post_json(self, url: str, payload: dict[str, object], *, timeout_seconds: float) -> SessionTransportResponse:
        self.calls.append((url, payload))
        return SessionTransportResponse(status_code=self.status_code, body='{"ok": true}', payload={"ok": True})


def test_spool_persists_jobs_and_flush_deletes_on_success(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=200)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
    )
    job = SessionShipJob(
        path="logs/sessions/claude/macbook/2026-04-12-s1.md",
        content="Decision: Rooster is the focus.",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
        captured_from="claude-code",
        updated="2026-04-12T10:00:00Z",
    )

    queued = shipper.enqueue(job)

    assert queued.exists()
    result = shipper.flush_pending()

    assert transport.calls
    assert not queued.exists()
    assert result.sent
    assert not result.failed


def test_spool_keeps_jobs_when_server_rejects(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=503)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
    )
    job = SessionShipJob(
        path="logs/sessions/codex/mini/2026-04-12-s2.md",
        content="Decision: keep the existing registry flow.",
        agent="codex",
        device="mini",
        session_id="s2",
        status="active",
        captured_from="codex",
        updated="2026-04-12T11:00:00Z",
    )

    queued = shipper.enqueue(job)
    result = shipper.flush_pending()

    assert queued.exists()
    assert not result.sent
    assert result.failed
    assert not result.dead_lettered


def test_spool_dead_letters_validation_rejects(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=400)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
    )
    queued = shipper.enqueue(
        SessionShipJob(
            path="logs/sessions/codex/mini/2026-04-12-s2.md",
            content="bad",
            agent="codex",
            device="mini",
            session_id="s2",
            status="invalid",
            captured_from="codex",
            updated="2026-04-12T11:00:00Z",
        )
    )

    result = shipper.flush_pending()

    assert not queued.exists()
    assert result.failed == (str(queued),)
    assert len(result.dead_lettered) == 1
    dead_letter = Path(result.dead_lettered[0])
    assert dead_letter.exists()
    payload = json.loads(dead_letter.read_text(encoding="utf-8"))
    assert payload["_dory_shipper"]["dead_letter_reason"] == '{"ok": true}'


def test_spool_replaces_pending_job_for_same_session(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    first = spool.enqueue(
        SessionShipJob(
            path="logs/sessions/claude/macbook/2026-04-12-s1.md",
            content="old",
            agent="claude",
            device="macbook",
            session_id="s1",
            status="active",
            captured_from="claude-code",
            updated="2026-04-12T10:00:00Z",
        )
    )
    second = spool.enqueue(
        SessionShipJob(
            path="logs/sessions/claude/macbook/2026-04-12-s1.md",
            content="new",
            agent="claude",
            device="macbook",
            session_id="s1",
            status="active",
            captured_from="claude-code",
            updated="2026-04-12T10:01:00Z",
        )
    )

    pending = spool.pending_paths()

    assert not first.exists()
    assert second.exists()
    assert len(pending) == 1
    assert spool.load(pending[0]).content == "new"


def test_spool_load_supports_legacy_target_key(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    legacy_path = spool.root / "legacy.json"
    legacy_path.write_text(
        """{
  "target": "logs/sessions/openclaw/macbook/2026-04-12-s1.md",
  "content": "legacy content",
  "agent": "openclaw",
  "device": "macbook",
  "session_id": "s1",
  "status": "active",
  "captured_from": "legacy",
  "updated": "2026-04-12T10:00:00Z"
}
""",
        encoding="utf-8",
    )

    job = spool.load(legacy_path)

    assert job.path == "logs/sessions/openclaw/macbook/2026-04-12-s1.md"
    assert job.content == "legacy content"


def test_flush_pending_marks_invalid_spool_job_as_failed(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=200)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
    )
    bad_job = spool.root / "bad.json"
    bad_job.write_text('{"content": "missing path"}\n', encoding="utf-8")

    result = shipper.flush_pending()

    assert not result.sent
    assert result.failed == (str(bad_job),)
    assert "missing path/target" in result.errors[0]
    assert not bad_job.exists()
    assert len(result.dead_lettered) == 1


def test_pending_paths_ignores_checkpoint_file(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    checkpoint = spool.root / "checkpoints.json"
    checkpoint.write_text('{"versions": {}}\n', encoding="utf-8")
    job_path = spool.enqueue(
        SessionShipJob(
            path="logs/sessions/openclaw/macbook/2026-04-12-s1.md",
            content="content",
            agent="openclaw",
            device="macbook",
            session_id="s1",
            status="active",
            captured_from="openclaw",
            updated="2026-04-12T10:00:00Z",
        )
    )

    pending = spool.pending_paths()

    assert checkpoint not in pending
    assert pending == (job_path,)


def test_flush_pending_records_transient_failure_attempt_metadata(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=503)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
    )
    queued = shipper.enqueue(
        SessionShipJob(
            path="logs/sessions/openclaw/macbook/2026-04-12-s1.md",
            content="content",
            agent="openclaw",
            device="macbook",
            session_id="s1",
            status="active",
            captured_from="openclaw",
            updated="2026-04-12T10:00:00Z",
        )
    )

    result = shipper.flush_pending()

    assert queued.exists()
    assert result.failed == (str(queued),)
    payload = json.loads(queued.read_text(encoding="utf-8"))
    assert payload["_dory_shipper"]["attempts"] == 1
    assert payload["_dory_shipper"]["last_error"] == '{"ok": true}'


def test_flush_pending_respects_max_flush_jobs(tmp_path: Path) -> None:
    spool = SessionSpool(tmp_path / "spool")
    transport = FakeTransport(status_code=200)
    shipper = SessionShipper(
        base_url="http://dory.local",
        spool=spool,
        transport=transport,
        max_flush_jobs=1,
    )
    for index in range(2):
        shipper.enqueue(
            SessionShipJob(
                path=f"logs/sessions/openclaw/macbook/2026-04-12-s{index}.md",
                content="content",
                agent="openclaw",
                device="macbook",
                session_id=f"s{index}",
                status="active",
                captured_from="openclaw",
                updated="2026-04-12T10:00:00Z",
            )
        )

    result = shipper.flush_pending()

    assert len(result.sent) == 1
    assert len(spool.pending_paths()) == 1


def test_default_shipper_accepts_custom_timeout(tmp_path: Path) -> None:
    shipper = build_default_shipper(
        base_url="http://dory.local",
        spool_root=tmp_path / "spool",
        timeout_seconds=2.5,
    )

    assert shipper.timeout_seconds == 2.5
