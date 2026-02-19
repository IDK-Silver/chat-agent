"""Tests for tools.builtin.contact_mapping."""

import pytest

from chat_agent.agent.contact_map import ContactMap
from chat_agent.tools.builtin.contact_mapping import (
    UPDATE_CONTACT_MAPPING_DEFINITION,
    create_update_contact_mapping,
)


@pytest.fixture()
def contact_map(tmp_path):
    return ContactMap(tmp_path / "cache")


def test_definition_name():
    assert UPDATE_CONTACT_MAPPING_DEFINITION.name == "update_contact_mapping"


def test_definition_required_params():
    assert set(UPDATE_CONTACT_MAPPING_DEFINITION.required) == {
        "channel", "sender_key", "name",
    }


def test_create_and_call(contact_map):
    fn = create_update_contact_mapping(contact_map)
    result = fn(channel="gmail", sender_key="a@example.com", name="alice")
    assert "OK" in result
    assert contact_map.resolve("gmail", "a@example.com") == "alice"


def test_result_format(contact_map):
    fn = create_update_contact_mapping(contact_map)
    result = fn(channel="line", sender_key="Ming", name="xiao-ming")
    assert result == "OK: line/Ming -> xiao-ming"
