#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _ensure_runtime_dependencies() -> None:
    try:
        import pydantic  # noqa: F401
        import pydantic_settings  # noqa: F401
    except ModuleNotFoundError:
        uv = shutil.which("uv")
        if uv is None or os.environ.get("DORY_CLIENT_SHIPPER_BOOTSTRAPPED") == "1":
            raise
        env = os.environ.copy()
        env["DORY_CLIENT_SHIPPER_BOOTSTRAPPED"] = "1"
        os.execvpe(uv, [uv, "run", "python", str(Path(__file__).resolve()), *sys.argv[1:]], env)


_ensure_runtime_dependencies()

from dory_core.session_capture import SessionCapture  # noqa: E402
from dory_core.session_cleaner import SessionCleaner  # noqa: E402
from dory_core.session_collectors import CollectorStateStore, build_collectors, collect_sessions  # noqa: E402
from dory_core.session_shipper import SessionShipResult, SessionShipper, build_default_shipper  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-discover, clean, and ship session logs to Dory.")
    parser.add_argument("--source", type=Path, help="Local session file to capture. Defaults to stdin.")
    parser.add_argument("--path", help="Corpus-relative target path under logs/sessions/...")
    parser.add_argument("--agent", help="Agent name, for example claude, codex, openclaw, or hermes")
    parser.add_argument("--device", default=os.environ.get("DORY_CLIENT_DEVICE", _default_device()), help="Device name")
    parser.add_argument("--session-id", help="Stable session id")
    parser.add_argument("--status", default="active", choices=["active", "interrupted", "done"])
    parser.add_argument("--captured-from", default="client-session-shipper")
    parser.add_argument("--updated", default="")
    parser.add_argument(
        "--spool-root",
        default=(
            os.environ.get("DORY_CLIENT_SPOOL_ROOT")
            or os.environ.get("DORY_SESSION_SPOOL_ROOT")
            or str(Path.home() / ".local" / "share" / "dory" / "spool")
        ),
    )
    parser.add_argument("--base-url", default=os.environ.get("DORY_HTTP_URL", "http://127.0.0.1:8766"))
    parser.add_argument(
        "--auth-token", default=os.environ.get("DORY_HTTP_TOKEN") or os.environ.get("DORY_CLIENT_AUTH_TOKEN")
    )
    parser.add_argument("--checkpoints-path", default=os.environ.get("DORY_CLIENT_CHECKPOINTS_PATH", ""))
    parser.add_argument(
        "--harnesses",
        default=os.environ.get("DORY_CLIENT_HARNESSES", "claude codex opencode openclaw hermes"),
    )
    parser.add_argument("--watch", action="store_true", help="Continuously collect and ship auto-discovered sessions.")
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("DORY_CLIENT_POLL_SECONDS", "15")))
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("DORY_CLIENT_SHIPPER_TIMEOUT_SECONDS", "10")),
    )
    parser.add_argument(
        "--max-flush-jobs",
        type=int,
        default=int(os.environ.get("DORY_CLIENT_MAX_FLUSH_JOBS", "100")),
        help="Maximum queued jobs to attempt per flush.",
    )
    parser.add_argument("--no-flush", action="store_true", help="Only enqueue locally.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    shipper = build_default_shipper(
        base_url=args.base_url,
        spool_root=Path(args.spool_root),
        token=args.auth_token,
        timeout_seconds=float(args.timeout_seconds),
        max_flush_jobs=int(args.max_flush_jobs),
    )

    if _is_manual_mode(args):
        payload = _run_manual_mode(args, shipper=shipper)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    harnesses = tuple(part.strip() for part in args.harnesses.split() if part.strip())
    if not harnesses:
        raise SystemExit("no harnesses selected for auto-discovery")
    checkpoints_path = (
        Path(args.checkpoints_path) if args.checkpoints_path else Path(args.spool_root) / "checkpoints.json"
    )

    if args.watch:
        while True:
            payload = _run_auto_mode(
                harnesses=harnesses,
                device=str(args.device),
                checkpoints_path=checkpoints_path,
                shipper=shipper,
                no_flush=bool(args.no_flush),
            )
            result = payload["result"]
            if not isinstance(result, dict):
                raise TypeError("session shipper result payload must be a mapping")
            result_payload = cast(dict[str, object], result)
            if payload["queued"] or result_payload["sent"] or result_payload["failed"]:
                print(json.dumps(payload, indent=2, sort_keys=True))
            time.sleep(max(args.poll_seconds, 1.0))
    else:
        payload = _run_auto_mode(
            harnesses=harnesses,
            device=str(args.device),
            checkpoints_path=checkpoints_path,
            shipper=shipper,
            no_flush=bool(args.no_flush),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_manual_mode(args: argparse.Namespace, *, shipper: SessionShipper) -> dict[str, object]:
    raw_text = _read_source(args.source)
    cleaner = SessionCleaner()
    cleaned = cleaner.clean(raw_text)
    updated = args.updated or datetime.now(tz=UTC).isoformat()

    capture = SessionCapture(
        path=str(args.path),
        agent=str(args.agent),
        device=str(args.device),
        session_id=str(args.session_id),
        status=str(args.status),
        captured_from=str(args.captured_from),
        updated=updated,
        raw_text=raw_text,
    )
    queued_path = shipper.enqueue(capture.to_ship_job(cleaner=cleaner, cleaned=cleaned))
    result = None if args.no_flush else shipper.flush_pending()
    return {
        "mode": "manual",
        "queued": [str(queued_path)],
        "cleaned_chars": cleaned.cleaned_chars,
        "dropped_lines": cleaned.dropped_lines,
        "redactions": cleaned.redactions,
        "result": _serialize_result(result),
    }


def _run_auto_mode(
    *,
    harnesses: tuple[str, ...],
    device: str,
    checkpoints_path: Path,
    shipper: SessionShipper,
    no_flush: bool,
) -> dict[str, object]:
    state_store = CollectorStateStore(checkpoints_path)
    state = state_store.load()
    collectors = build_collectors(harnesses)
    captures = collect_sessions(collectors, device=device, state=state)
    queued: list[str] = []
    for collected in captures:
        queued.append(str(shipper.enqueue(collected.capture.to_ship_job())))
        state.update(collected.source_key, collected.source_version)
    state_store.save(state)
    result = None if no_flush else shipper.flush_pending()
    return {
        "mode": "auto",
        "harnesses": list(harnesses),
        "checkpoints_path": str(checkpoints_path),
        "captures": [
            {
                "agent": collected.capture.agent,
                "path": collected.capture.path,
                "session_id": collected.capture.session_id,
                "updated": collected.capture.updated,
                "captured_from": collected.capture.captured_from,
            }
            for collected in captures
        ],
        "queued": queued,
        "result": _serialize_result(result),
    }


def _serialize_result(result: SessionShipResult | None) -> dict[str, object]:
    if result is None:
        return {"sent": [], "failed": [], "errors": [], "dead_lettered": []}
    return {
        "sent": list(result.sent),
        "failed": list(result.failed),
        "errors": list(result.errors),
        "dead_lettered": list(result.dead_lettered),
    }


def _read_source(source: Path | None) -> str:
    if source is not None:
        return source.read_text(encoding="utf-8")
    return sys.stdin.read()


def _is_manual_mode(args: argparse.Namespace) -> bool:
    return all(getattr(args, field) for field in ("path", "agent", "session_id"))


def _default_device() -> str:
    return socket.gethostname().split(".")[0]


if __name__ == "__main__":
    raise SystemExit(main())
