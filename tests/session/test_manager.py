"""Tests for SessionManager."""

from datetime import datetime, timezone as tz
from pathlib import Path

import pytest

from chat_agent.llm.schema import Message, ToolCall
from chat_agent.session.manager import SessionManager


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
        msg = Message(role="user", content="hello", timestamp=datetime.now(tz.utc))
        mgr.append_message(msg)

        messages = mgr.load(sid)
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].content == "hello"

    def test_multiple_messages(self, mgr: SessionManager):
        sid = mgr.create("alice", "Alice")
        mgr.append_message(Message(role="user", content="hi"))
        mgr.append_message(Message(role="assistant", content="hello"))
        mgr.append_message(Message(role="user", content="bye"))

        messages = mgr.load(sid)
        assert len(messages) == 3
        assert [m.role for m in messages] == ["user", "assistant", "user"]

    def test_updates_message_count(self, mgr: SessionManager, sessions_dir: Path):
        sid = mgr.create("alice", "Alice")
        mgr.append_message(Message(role="user", content="one"))
        mgr.append_message(Message(role="assistant", content="two"))

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
            Message(role="assistant", content=None, tool_calls=tool_calls)
        )
        mgr.append_message(
            Message(
                role="tool",
                content='{"time": "12:00"}',
                tool_call_id="tc1",
                name="get_time",
            )
        )

        messages = mgr.load(sid)
        assert len(messages) == 2
        assert messages[0].tool_calls is not None
        assert messages[0].tool_calls[0].name == "get_time"
        assert messages[0].tool_calls[0].arguments == {"tz": "UTC"}
        assert messages[1].role == "tool"
        assert messages[1].tool_call_id == "tc1"

    def test_append_without_create_is_noop(self, sessions_dir: Path):
        mgr = SessionManager(sessions_dir)
        # No create() called, append should silently do nothing
        mgr.append_message(Message(role="user", content="ignored"))
        # No crash, no files created beyond the sessions dir itself
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
        mgr.append_message(Message(role="user", content="first"))

        s2 = mgr.create("alice", "Alice")
        mgr.append_message(Message(role="user", content="second"))

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
        messages = mgr.load(sid)
        assert messages == []
