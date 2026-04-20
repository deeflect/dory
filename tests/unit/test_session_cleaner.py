from dory_core.session_cleaner import SessionCleaner, clean_session_text


def test_clean_session_text_redacts_secrets_and_drops_tool_noise() -> None:
    fake_secret_assignment = "OPENROUTER_API_" + "KEY=not-a-real-secret"
    raw = f"""
⏺ dory - dory_wake (MCP)
tool: bash
{fake_secret_assignment}
Bearer abc.def.ghi
User: use Rooster this week
Assistant: Decision: Rooster is the active focus.
""".strip()

    cleaned = clean_session_text(raw)

    assert "dory_wake (MCP)" not in cleaned.text
    assert "tool: bash" not in cleaned.text
    assert "not-a-real-secret" not in cleaned.text
    assert "Bearer abc.def.ghi" not in cleaned.text
    assert "OPENROUTER_API_KEY=[REDACTED]" in cleaned.text
    assert "Rooster is the active focus." in cleaned.text
    assert cleaned.dropped_lines == 2
    assert cleaned.redactions >= 2


def test_cleaner_preserves_useful_non_tool_context() -> None:
    cleaner = SessionCleaner()
    raw = """
⏺ No active project context came back from recent sessions.

We should use Dory wake first.
""".strip()

    cleaned = cleaner.clean(raw)

    assert "No active project context came back from recent sessions." in cleaned.text
    assert "We should use Dory wake first." in cleaned.text
