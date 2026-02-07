"""Tests for post-review retry helpers in CLI app."""

from chat_agent.cli.app import _build_retry_reminder, _normalize_violation


def test_normalize_violation_categories():
    assert (
        _normalize_violation(
            "AI did not use write_file to save to memory/agent/knowledge/"
        )
        == "knowledge_write"
    )
    assert _normalize_violation("Topic shifted but no short-term.md update") == "short_term_update"
    assert _normalize_violation("AI stated any time without get_current_time") == "time_check"
    assert (
        _normalize_violation("AI did not use execute_shell with grep before answering")
        == "memory_grep"
    )
    assert (
        _normalize_violation("Conversation exceeded 10 exchanges but no inner-state.md update")
        == "inner_state_update"
    )


def test_build_retry_reminder_contains_guidance():
    guidance = "Use grep first, then write_file to knowledge."
    reminder = _build_retry_reminder(guidance)

    assert "COMPLIANCE RETRY" in reminder
    assert guidance in reminder
