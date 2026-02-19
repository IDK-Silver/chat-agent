"""Tests for agent.contact_map."""

import json

import pytest

from chat_agent.agent.contact_map import ContactMap


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "cache"


def test_resolve_unknown_returns_none(cache_dir):
    cm = ContactMap(cache_dir)
    assert cm.resolve("gmail", "unknown@example.com") is None


def test_update_and_resolve(cache_dir):
    cm = ContactMap(cache_dir)
    cm.update("gmail", "a@example.com", "alice")
    assert cm.resolve("gmail", "a@example.com") == "alice"


def test_update_overwrites(cache_dir):
    cm = ContactMap(cache_dir)
    cm.update("gmail", "a@example.com", "alice")
    cm.update("gmail", "a@example.com", "alice_new")
    assert cm.resolve("gmail", "a@example.com") == "alice_new"


def test_persistence_roundtrip(cache_dir):
    cm1 = ContactMap(cache_dir)
    cm1.update("gmail", "a@example.com", "alice")
    cm1.update("line", "Ming", "xiao-ming")

    # Create a new instance from the same path
    cm2 = ContactMap(cache_dir)
    assert cm2.resolve("gmail", "a@example.com") == "alice"
    assert cm2.resolve("line", "Ming") == "xiao-ming"


def test_corrupt_file_degrades_gracefully(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "contact_map.json").write_text("NOT JSON!!!", encoding="utf-8")

    cm = ContactMap(cache_dir)
    assert cm.resolve("gmail", "a@example.com") is None
    # Should still be able to update after degraded load
    cm.update("gmail", "a@example.com", "alice")
    assert cm.resolve("gmail", "a@example.com") == "alice"


def test_multiple_channels_independent(cache_dir):
    cm = ContactMap(cache_dir)
    cm.update("gmail", "a@example.com", "alice")
    cm.update("line", "a@example.com", "different_alice")

    assert cm.resolve("gmail", "a@example.com") == "alice"
    assert cm.resolve("line", "a@example.com") == "different_alice"


def test_missing_cache_dir_created_on_update(cache_dir):
    assert not cache_dir.exists()
    cm = ContactMap(cache_dir)
    cm.update("gmail", "a@example.com", "alice")
    assert cache_dir.exists()
    assert (cache_dir / "contact_map.json").exists()


def test_file_format(cache_dir):
    cm = ContactMap(cache_dir)
    cm.update("gmail", "a@example.com", "alice")
    data = json.loads((cache_dir / "contact_map.json").read_text(encoding="utf-8"))
    assert data == {"gmail": {"a@example.com": "alice"}}
