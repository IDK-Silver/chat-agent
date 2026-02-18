"""Tests for PersistentPriorityQueue."""

import json
from datetime import datetime, timezone

import pytest

from chat_agent.agent.queue import PersistentPriorityQueue, _serialize, _deserialize
from chat_agent.agent.schema import InboundMessage, ShutdownSentinel


def _make_msg(content="hi", channel="cli", priority=0, sender="u"):
    return InboundMessage(
        channel=channel,
        content=content,
        priority=priority,
        sender=sender,
    )


class TestSerialize:
    def test_roundtrip(self):
        msg = _make_msg(content="hello")
        data = _serialize(msg)
        restored = _deserialize(data)
        assert restored.channel == msg.channel
        assert restored.content == msg.content
        assert restored.priority == msg.priority
        assert restored.sender == msg.sender

    def test_serialize_contains_required_fields(self):
        msg = _make_msg()
        data = _serialize(msg)
        assert set(data.keys()) == {
            "channel", "content", "priority", "sender", "metadata", "timestamp",
        }


class TestPutAndGet:
    def test_put_get_single(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        msg = _make_msg(content="test")
        q.put(msg)
        got, receipt = q.get()
        assert got.content == "test"
        assert receipt is not None

    def test_priority_ordering(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="low", priority=2))
        q.put(_make_msg(content="high", priority=0))
        q.put(_make_msg(content="mid", priority=1))

        r1, _ = q.get()
        r2, _ = q.get()
        r3, _ = q.get()
        assert r1.content == "high"
        assert r2.content == "mid"
        assert r3.content == "low"

    def test_fifo_within_same_priority(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="first", priority=0))
        q.put(_make_msg(content="second", priority=0))
        q.put(_make_msg(content="third", priority=0))

        r1, _ = q.get()
        r2, _ = q.get()
        r3, _ = q.get()
        assert r1.content == "first"
        assert r2.content == "second"
        assert r3.content == "third"


class TestPersistence:
    def test_pending_file_created(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="persist"))
        pending = list((tmp_path / "q" / "pending").iterdir())
        assert len(pending) == 1
        data = json.loads(pending[0].read_text())
        assert data["content"] == "persist"

    def test_get_moves_to_active(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="move"))
        _, receipt = q.get()
        assert receipt is not None
        assert receipt.parent.name == "active"
        pending = list((tmp_path / "q" / "pending").iterdir())
        assert len(pending) == 0

    def test_ack_deletes_file(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="delete"))
        _, receipt = q.get()
        q.ack(receipt)
        active = list((tmp_path / "q" / "active").iterdir())
        assert len(active) == 0

    def test_ack_none_is_noop(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.ack(None)  # should not raise


class TestRecovery:
    def test_active_recovered_on_startup(self, tmp_path):
        """Simulate crash: leave a file in active/, verify it's recovered."""
        qdir = tmp_path / "q"
        (qdir / "pending").mkdir(parents=True)
        (qdir / "active").mkdir(parents=True)

        msg = _make_msg(content="crashed")
        data = json.dumps(_serialize(msg))
        (qdir / "active" / "0000_00000001.json").write_text(data)

        q = PersistentPriorityQueue(qdir)
        assert q.pending_count() == 1
        got, _ = q.get()
        assert got.content == "crashed"

    def test_discard_channels(self, tmp_path):
        """CLI messages are discarded on startup, others kept."""
        qdir = tmp_path / "q"
        (qdir / "pending").mkdir(parents=True)
        (qdir / "active").mkdir(parents=True)

        cli_msg = _make_msg(content="stale-cli", channel="cli")
        line_msg = _make_msg(content="keep-line", channel="line", priority=1)
        (qdir / "pending" / "0000_00000001.json").write_text(
            json.dumps(_serialize(cli_msg)),
        )
        (qdir / "pending" / "0001_00000002.json").write_text(
            json.dumps(_serialize(line_msg)),
        )

        q = PersistentPriorityQueue(qdir, discard_channels={"cli"})
        assert q.pending_count() == 1
        got, _ = q.get()
        assert got.content == "keep-line"

    def test_corrupt_file_skipped(self, tmp_path):
        qdir = tmp_path / "q"
        (qdir / "pending").mkdir(parents=True)
        (qdir / "active").mkdir(parents=True)

        (qdir / "pending" / "0000_00000001.json").write_text("not json")
        q = PersistentPriorityQueue(qdir)
        assert q.pending_count() == 0


class TestShutdownSentinel:
    def test_sentinel_not_persisted(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(ShutdownSentinel(graceful=True))
        pending = list((tmp_path / "q" / "pending").iterdir())
        assert len(pending) == 0

    def test_sentinel_has_highest_priority(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg(content="normal", priority=0))
        q.put(ShutdownSentinel())

        got, receipt = q.get()
        assert isinstance(got, ShutdownSentinel)
        assert receipt is None

    def test_sentinel_graceful_flag(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(ShutdownSentinel(graceful=False))
        got, _ = q.get()
        assert isinstance(got, ShutdownSentinel)
        assert got.graceful is False


class TestPendingCount:
    def test_empty(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        assert q.pending_count() == 0

    def test_after_put(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg())
        assert q.pending_count() == 1

    def test_after_get(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(_make_msg())
        q.get()
        assert q.pending_count() == 0
