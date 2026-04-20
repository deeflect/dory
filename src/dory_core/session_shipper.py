from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request
import json
import uuid


class SessionSpoolJobError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionShipJob:
    path: str
    content: str
    agent: str
    device: str
    session_id: str
    status: str
    captured_from: str
    updated: str

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SessionTransportResponse:
    status_code: int
    body: str
    payload: dict[str, Any] | None = None


class SessionTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], *, timeout_seconds: float) -> SessionTransportResponse: ...


@dataclass(frozen=True, slots=True)
class UrllibSessionTransport:
    token: str | None = None

    def post_json(self, url: str, payload: dict[str, Any], *, timeout_seconds: float) -> SessionTransportResponse:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                text = response.read().decode("utf-8")
                parsed = json.loads(text) if text else {}
                return SessionTransportResponse(
                    status_code=response.status,
                    body=text,
                    payload=parsed if isinstance(parsed, dict) else None,
                )
        except error.HTTPError as exc:
            return SessionTransportResponse(
                status_code=exc.code,
                body=exc.read().decode("utf-8", errors="replace"),
            )


@dataclass(frozen=True, slots=True)
class SessionSpool:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(self, job: SessionShipJob) -> Path:
        self._delete_replaced_jobs(job)
        target = self.root / _job_filename(job)
        target.write_text(json.dumps(job.as_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def pending_paths(self) -> tuple[Path, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path
                for path in self.root.glob("*.json")
                if path.is_file() and path.name != "checkpoints.json"
            )
        )

    def load(self, path: Path) -> SessionShipJob:
        payload = json.loads(path.read_text(encoding="utf-8"))
        target_path = payload.get("path") or payload.get("target")
        if not isinstance(target_path, str) or not target_path.strip():
            raise SessionSpoolJobError(f"spool job missing path/target: {path}")
        return SessionShipJob(
            path=target_path.strip(),
            content=str(payload.get("content", "")),
            agent=str(payload.get("agent", "")),
            device=str(payload.get("device", "")),
            session_id=str(payload.get("session_id", "")),
            status=str(payload.get("status", "active")),
            captured_from=str(payload.get("captured_from", "legacy-spool")),
            updated=str(payload.get("updated", "")),
        )

    def delete(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def _delete_replaced_jobs(self, job: SessionShipJob) -> None:
        for path in self.pending_paths():
            try:
                pending = self.load(path)
            except Exception:
                continue
            if pending.path == job.path and pending.session_id == job.session_id:
                self.delete(path)


@dataclass(frozen=True, slots=True)
class SessionShipResult:
    sent: tuple[str, ...]
    failed: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionShipper:
    base_url: str
    spool: SessionSpool
    transport: SessionTransport
    ingest_path: str = "/v1/session-ingest"
    timeout_seconds: float = 10.0

    def enqueue(self, job: SessionShipJob) -> Path:
        return self.spool.enqueue(job)

    def flush_pending(self) -> SessionShipResult:
        sent: list[str] = []
        failed: list[str] = []
        errors: list[str] = []

        for path in self.spool.pending_paths():
            try:
                job = self.spool.load(path)
            except SessionSpoolJobError as exc:
                failed.append(str(path))
                errors.append(str(exc))
                continue
            try:
                response = self.transport.post_json(
                    self._endpoint_url(),
                    job.as_payload(),
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception as exc:  # pragma: no cover - transport failures are validated via tests
                failed.append(str(path))
                errors.append(str(exc))
                continue

            if response.status_code >= 400:
                failed.append(str(path))
                errors.append(response.body[:500])
                continue

            self.spool.delete(path)
            sent.append(str(path))

        return SessionShipResult(sent=tuple(sent), failed=tuple(failed), errors=tuple(errors))

    def ship(self, job: SessionShipJob) -> SessionShipResult:
        self.enqueue(job)
        return self.flush_pending()

    def _endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.ingest_path}"


def build_default_shipper(
    *,
    base_url: str,
    spool_root: Path,
    token: str | None = None,
) -> SessionShipper:
    return SessionShipper(
        base_url=base_url,
        spool=SessionSpool(Path(spool_root)),
        transport=UrllibSessionTransport(token=token),
    )


def _job_filename(job: SessionShipJob) -> str:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_session_id = _sanitize_component(job.session_id)
    safe_agent = _sanitize_component(job.agent)
    return f"{timestamp}-{safe_agent}-{safe_session_id}-{uuid.uuid4().hex}.json"


def _sanitize_component(value: str) -> str:
    cleaned = [character if character.isalnum() or character in {"-", "_"} else "_" for character in value]
    candidate = "".join(cleaned).strip("_")
    return candidate or "session"
