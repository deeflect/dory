from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dory_core.session_collectors import (
    ClaudeProjectsCollector,
    CodexSessionsCollector,
    CollectorState,
    HermesSessionsCollector,
    OpenClawSessionsCollector,
    OpenCodeCollector,
)


def test_claude_projects_collector_collects_main_session_text(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "projects" / "palace"
    root.mkdir(parents=True)
    session_path = root / "1234.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "timestamp": "2026-04-12T12:00:00Z",
                        "cwd": "/repo",
                        "gitBranch": "main",
                        "sessionId": "1234",
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-04-12T12:01:00Z",
                        "message": {"role": "user", "content": "what are we working on today"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-04-12T12:02:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "text": "ignored"},
                                {"type": "text", "text": "Rooster is the active focus this week."},
                                {"type": "tool_use", "name": "Read"},
                            ],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    collector = ClaudeProjectsCollector(root=root)
    results = collector.collect(device="macbook", state=CollectorState())

    assert len(results) == 1
    capture = results[0].capture
    assert capture.path == "logs/sessions/claude/macbook/2026-04-12-1234.md"
    assert "what are we working on today" in capture.raw_text
    assert "Rooster is the active focus this week." in capture.raw_text
    assert "tool_use" not in capture.raw_text


def test_codex_sessions_collector_collects_session_file(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions" / "2026" / "04" / "12"
    root.mkdir(parents=True)
    session_path = root / "rollout-2026-04-12T18-00-00-s1.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T18:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "s1",
                            "cwd": "/repo",
                            "agent_nickname": "worker",
                            "agent_role": "worker",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T18:00:01Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "remember this decision"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T18:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "We are using Dory as shared memory."}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T18:00:03Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "arguments": "{\"cmd\":\"pwd\"}",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    collector = CodexSessionsCollector(root=root.parent.parent.parent)
    results = collector.collect(device="mini", state=CollectorState())

    assert len(results) == 1
    capture = results[0].capture
    assert capture.path == "logs/sessions/codex/mini/2026-04-12-s1.md"
    assert "remember this decision" in capture.raw_text
    assert "We are using Dory as shared memory." in capture.raw_text
    assert "exec_command" not in capture.raw_text


def test_opencode_collector_reads_session_text_parts(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            share_url TEXT,
            summary_additions INTEGER,
            summary_deletions INTEGER,
            summary_files INTEGER,
            summary_diffs TEXT,
            revert TEXT,
            permission TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER,
            workspace_id TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO session(id, project_id, slug, directory, title, version, time_created, time_updated, time_archived)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        ("ses_1", "proj_1", "slug", "/repo", "Review", "1", 1774408000000, 1774409000000),
    )
    connection.execute(
        """
        INSERT INTO message(id, session_id, time_created, time_updated, data)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("msg_1", "ses_1", 1774408000000, 1774408000000, json.dumps({"role": "user"})),
    )
    connection.execute(
        """
        INSERT INTO part(id, message_id, session_id, time_created, time_updated, data)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("prt_1", "msg_1", "ses_1", 1774408000000, 1774408000000, json.dumps({"type": "text", "text": "review this page"})),
    )
    connection.execute(
        """
        INSERT INTO message(id, session_id, time_created, time_updated, data)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("msg_2", "ses_1", 1774408100000, 1774408100000, json.dumps({"role": "assistant"})),
    )
    connection.executemany(
        """
        INSERT INTO part(id, message_id, session_id, time_created, time_updated, data)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("prt_2", "msg_2", "ses_1", 1774408100000, 1774408100000, json.dumps({"type": "reasoning", "text": "ignore"})),
            ("prt_3", "msg_2", "ses_1", 1774408200000, 1774408200000, json.dumps({"type": "text", "text": "The style is good but the CTA is weak."})),
        ],
    )
    connection.commit()
    connection.close()

    collector = OpenCodeCollector(db_path=db_path)
    results = collector.collect(device="macbook", state=CollectorState())

    assert len(results) == 1
    capture = results[0].capture
    assert capture.path == "logs/sessions/opencode/macbook/2026-03-25-ses-1.md"
    assert "review this page" in capture.raw_text
    assert "The style is good but the CTA is weak." in capture.raw_text
    assert "reasoning" not in capture.raw_text


def test_openclaw_sessions_collector_reads_agent_session_jsonl(tmp_path: Path) -> None:
    root = tmp_path / ".openclaw" / "agents" / "agent-1" / "sessions"
    root.mkdir(parents=True)
    session_path = root / "ses-1.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T10:00:00Z",
                        "sessionId": "ses-1",
                        "cwd": "/repo",
                        "gitBranch": "main",
                        "type": "user_message",
                        "message": {"role": "user", "content": [{"type": "text", "text": "remember this pricing plan"}]},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T10:00:02Z",
                        "type": "tool_result",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "ignored tool"}]},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T10:00:03Z",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Clawsy stays on the small Hetzner VPS."}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    collector = OpenClawSessionsCollector(root=root.parent.parent)
    results = collector.collect(device="macbook", state=CollectorState())

    assert len(results) == 1
    capture = results[0].capture
    assert capture.path == "logs/sessions/openclaw/macbook/2026-04-12-ses-1.md"
    assert "remember this pricing plan" in capture.raw_text
    assert "Clawsy stays on the small Hetzner VPS." in capture.raw_text
    assert "ignored tool" not in capture.raw_text


def test_hermes_sessions_collector_reads_transcript_jsonl(tmp_path: Path) -> None:
    root = tmp_path / ".hermes" / "sessions" / "2026" / "04" / "12"
    root.mkdir(parents=True)
    session_path = root / "session-a.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:00Z",
                        "session_id": "session-a",
                        "cwd": "/repo",
                        "role": "user",
                        "content": [{"type": "text", "text": "what did we decide for Rooster?"}],
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:01Z",
                        "role": "assistant",
                        "content": [
                            {"type": "reasoning", "text": "ignore"},
                            {"type": "text", "text": "Rooster is the active focus this week."},
                        ],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    state_db_path = tmp_path / ".hermes" / "state.db"
    state_db_path.write_text("", encoding="utf-8")

    collector = HermesSessionsCollector(root=root.parent.parent.parent, state_db_path=state_db_path)
    results = collector.collect(device="mini", state=CollectorState())

    assert len(results) == 1
    capture = results[0].capture
    assert capture.path == "logs/sessions/hermes/mini/2026-04-12-session-a.md"
    assert "what did we decide for Rooster?" in capture.raw_text
    assert "Rooster is the active focus this week." in capture.raw_text
    assert "reasoning" not in capture.raw_text
    assert str(state_db_path) in capture.raw_text


def test_collectors_skip_unchanged_sources(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    session_path = root / "1234.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-04-12T12:01:00Z",
                "sessionId": "1234",
                "message": {"role": "user", "content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    collector = ClaudeProjectsCollector(root=root)
    first = collector.collect(device="macbook", state=CollectorState())
    state = CollectorState({first[0].source_key: first[0].source_version})

    second = collector.collect(device="macbook", state=state)

    assert len(first) == 1
    assert second == ()
