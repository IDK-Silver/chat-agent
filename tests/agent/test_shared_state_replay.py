from datetime import datetime, timezone
from pathlib import Path

from chat_agent.agent.shared_state import SharedStateStore
from chat_agent.agent.shared_state_replay import rebuild_shared_state_from_sessions
from chat_agent.llm.schema import Message, ToolCall
from chat_agent.session.schema import SessionEntry


def _append_jsonl(path: Path, entries: list[SessionEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(e.model_dump_json() + "\n")


def test_replay_rebuilds_only_successful_send_message(tmp_path):
    sessions_dir = tmp_path / "session" / "brain"
    sdir = sessions_dir / "20260224_000000_aaaaaa"
    jsonl = sdir / "messages.jsonl"

    user_entry = SessionEntry(
        message=Message(
            role="user",
            content="hi",
            timestamp=datetime(2026, 2, 24, 13, 55, tzinfo=timezone.utc),
        ),
        channel="discord",
        sender="idksilver",
        metadata={"is_dm": True, "author_id": "123", "channel_id": "dmch"},
    )
    assistant_entry = SessionEntry(
        message=Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="send_message",
                    arguments={"channel": "discord", "body": "OK1"},
                ),
                ToolCall(
                    id="c2",
                    name="send_message",
                    arguments={"channel": "discord", "body": "FAIL"},
                ),
            ],
            timestamp=datetime(2026, 2, 24, 13, 55, 1, tzinfo=timezone.utc),
        )
    )
    tool_ok = SessionEntry(
        message=Message(
            role="tool",
            content="OK: sent to discord (老公)",
            tool_call_id="c1",
            name="send_message",
            timestamp=datetime(2026, 2, 24, 13, 55, 2, tzinfo=timezone.utc),
        )
    )
    tool_fail = SessionEntry(
        message=Message(
            role="tool",
            content="Error: boom",
            tool_call_id="c2",
            name="send_message",
            timestamp=datetime(2026, 2, 24, 13, 55, 3, tzinfo=timezone.utc),
        )
    )
    _append_jsonl(jsonl, [user_entry, assistant_entry, tool_ok, tool_fail])

    store = SharedStateStore(tmp_path / "shared_state.json")
    stats = rebuild_shared_state_from_sessions(sessions_dir, store=store)

    assert stats.sessions_scanned == 1
    assert stats.send_message_calls_seen == 2
    assert stats.send_message_successes_replayed == 1
    assert store.get_current_rev("discord:dm:123") == 1

