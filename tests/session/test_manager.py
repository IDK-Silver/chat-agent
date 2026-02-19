"""Tests for SessionManager."""

from datetime import datetime, timezone as tz
from pathlib import Path

import pytest

from chat_agent.llm.schema import Message, ToolCall
from chat_agent.session.manager import SessionManager
from chat_agent.session.schema import SessionEntry


def _entry(msg: Message, *, channel: str | None = None, sender: str | None = None) -> SessionEntry:
    """Wrap a Message in a SessionEntry for testing."""
    return SessionEntry(message=msg, channel=channel, sender=sender)


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


@pytest.fixture
def mgr(sessions_dir: Path) -> SessionManager:
    return SessionManager(sessions_dir)


class TestCreate:
    def test_creates_directory_and_meta(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("alice", "Alice")
        session_dir = sessions_dir / sid
        assert session_dir.is_dir()
        assert (session_dir / "meta.json").exists()

    def test_meta_fields(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("bob", "Bob")
        from chat_agent.session.schema import SessionMetadata

        meta = SessionMetadata.model_validate_json(
            (sessions_dir / sid / "meta.json").read_text()
        )
        assert meta.session_id == sid
        assert meta.user_id == "bob"
        assert meta.display_name == "Bob"
        assert meta.status == "active"
        assert meta.message_count == 0

    def test_sets_current_session(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        assert mgr.current_session_id == sid


class TestAppendAndLoad:
    def test_append_and_load_roundtrip(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        entry = _entry(
            Message(role="user", content="hello", timestamp=datetime.now(tz.utc)),
            channel="cli",
            sender="alice",
        )
        mgr.append_message(entry)

        entries = mgr.load(sid)
        assert len(entries) == 1
        assert entries[0].role == "user"
        assert entries[0].content == "hello"
        assert entries[0].channel == "cli"
        assert entries[0].sender == "alice"

    def test_multiple_messages(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        mgr.append_message(_entry(Message(role="user", content="hi"), channel="cli"))
        mgr.append_message(_entry(Message(role="assistant", content="hello")))
        mgr.append_message(_entry(Message(role="user", content="bye"), channel="cli"))

        entries = mgr.load(sid)
        assert len(entries) == 3
        assert [e.role for e in entries] == ["user", "assistant", "user"]

    def test_updates_message_count(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("alice", "Alice")
        mgr.append_message(_entry(Message(role="user", content="one")))
        mgr.append_message(_entry(Message(role="assistant", content="two")))

        from chat_agent.session.schema import SessionMetadata

        meta = SessionMetadata.model_validate_json(
            (sessions_dir / sid / "meta.json").read_text()
        )
        assert meta.message_count == 2

    def test_tool_call_message_roundtrip(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        tool_calls = [
            ToolCall(id="tc1", name="get_time", arguments={"tz": "UTC"}),
        ]
        mgr.append_message(
            _entry(Message(role="assistant", content=None, tool_calls=tool_calls))
        )
        mgr.append_message(
            _entry(Message(
                role="tool",
                content='{"time": "12:00"}',
                tool_call_id="tc1",
                name="get_time",
            ))
        )

        entries = mgr.load(sid)
        assert len(entries) == 2
        assert entries[0].tool_calls is not None
        assert entries[0].tool_calls[0].name == "get_time"
        assert entries[0].tool_calls[0].arguments == {"tz": "UTC"}
        assert entries[1].role == "tool"
        assert entries[1].tool_call_id == "tc1"

    def test_append_without_create_is_noop(self, sessions_dir: Path):
        mgr = SessionManager(sessions_dir)
        mgr.append_message(_entry(Message(role="user", content="ignored")))
        assert list(sessions_dir.iterdir()) == []


class TestFinalize:
    def test_finalize_completed(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("alice", "Alice")
        mgr.finalize("completed")

        from chat_agent.session.schema import SessionMetadata

        meta = SessionMetadata.model_validate_json(
            (sessions_dir / sid / "meta.json").read_text()
        )
        assert meta.status == "completed"

    def test_finalize_exited(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("alice", "Alice")
        mgr.finalize("exited")

        from chat_agent.session.schema import SessionMetadata

        meta = SessionMetadata.model_validate_json(
            (sessions_dir / sid / "meta.json").read_text()
        )
        assert meta.status == "exited"


class TestListRecent:
    def test_empty(self, mgr: SessionManager):
        assert mgr.list_recent("alice") == []

    def test_lists_user_sessions(self, mgr: SessionManager):
        mgr.create("alice", "Alice")
        mgr.create("bob", "Bob")
        mgr.create("alice", "Alice2")

        results = mgr.list_recent("alice")
        assert len(results) == 2
        assert all(m.user_id == "alice" for m in results)

    def test_sorted_by_updated_at_desc(self, mgr: SessionManager):
        s1 = mgr.create("alice", "Alice")
        mgr.append_message(_entry(Message(role="user", content="first")))

        s2 = mgr.create("alice", "Alice")
        mgr.append_message(_entry(Message(role="user", content="second")))

        results = mgr.list_recent("alice")
        assert results[0].session_id == s2
        assert results[1].session_id == s1

    def test_limit(self, mgr: SessionManager):
        for _ in range(5):
            mgr.create("alice", "Alice")

        results = mgr.list_recent("alice", limit=3)
        assert len(results) == 3


class TestLoadNonexistent:
    def test_raises_on_missing(self, mgr: SessionManager):
        with pytest.raises(FileNotFoundError):
            mgr.load("nonexistent_session")

    def test_load_empty_session(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        entries = mgr.load(sid)
        assert entries == []


class TestRewriteMessages:
    def test_rewrite(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        mgr.append_message(_entry(Message(role="user", content="one")))
        mgr.append_message(_entry(Message(role="assistant", content="two")))

        # Rewrite with fewer entries
        mgr.rewrite_messages([_entry(Message(role="user", content="only"))])
        entries = mgr.load(sid)
        assert len(entries) == 1
        assert entries[0].content == "only"
