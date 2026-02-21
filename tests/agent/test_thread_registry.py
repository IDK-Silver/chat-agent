"""Tests for agent.thread_registry."""

import json

import pytest

from chat_agent.agent.thread_registry import ThreadRegistry


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "cache"


def test_get_unknown_returns_none(cache_dir):
    reg = ThreadRegistry(cache_dir)
    assert reg.get("gmail", "unknown@example.com") is None


def test_update_and_get_roundtrip(cache_dir):
    reg = ThreadRegistry(cache_dir)
    data = {"thread_id": "t1", "message_id": "m1", "subject": "Re: Hi"}
    reg.update("gmail", "a@example.com", data)
    assert reg.get("gmail", "a@example.com") == data


def test_get_returns_copy(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    result = reg.get("gmail", "a@example.com")
    result["thread_id"] = "mutated"
    assert reg.get("gmail", "a@example.com")["thread_id"] == "t1"


def test_update_overwrites(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    reg.update("gmail", "a@example.com", {"thread_id": "t2"})
    assert reg.get("gmail", "a@example.com")["thread_id"] == "t2"


def test_persistence_roundtrip(cache_dir):
    reg1 = ThreadRegistry(cache_dir)
    reg1.update("gmail", "a@example.com", {"thread_id": "t1"})

    reg2 = ThreadRegistry(cache_dir)
    assert reg2.get("gmail", "a@example.com")["thread_id"] == "t1"


def test_corrupt_file_degrades_gracefully(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "thread_registry.json").write_text("NOT JSON!!!")

    reg = ThreadRegistry(cache_dir)
    assert reg.get("gmail", "a@example.com") is None
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    assert reg.get("gmail", "a@example.com")["thread_id"] == "t1"


def test_multiple_channels_independent(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "g1"})
    reg.update("discord", "a@example.com", {"channel_id": "d1"})

    assert reg.get("gmail", "a@example.com")["thread_id"] == "g1"
    assert reg.get("discord", "a@example.com")["channel_id"] == "d1"


def test_missing_cache_dir_created_on_update(cache_dir):
    assert not cache_dir.exists()
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    assert cache_dir.exists()
    assert (cache_dir / "thread_registry.json").exists()


def test_file_format(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1", "subject": "Re: Hi"})
    data = json.loads(
        (cache_dir / "thread_registry.json").read_text(encoding="utf-8")
    )
    assert data == {
        "gmail": {"a@example.com": {"thread_id": "t1", "subject": "Re: Hi"}}
    }


def test_multiple_contacts_same_channel(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    reg.update("gmail", "b@example.com", {"thread_id": "t2"})

    assert reg.get("gmail", "a@example.com")["thread_id"] == "t1"
    assert reg.get("gmail", "b@example.com")["thread_id"] == "t2"


def test_get_unknown_channel_returns_none(cache_dir):
    reg = ThreadRegistry(cache_dir)
    reg.update("gmail", "a@example.com", {"thread_id": "t1"})
    assert reg.get("discord", "a@example.com") is None
