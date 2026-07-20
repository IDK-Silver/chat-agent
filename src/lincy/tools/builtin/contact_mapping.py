"""Contact mapping tool: brain caches sender-to-name resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...llm.schema import ToolDefinition, ToolParameter

if TYPE_CHECKING:
    from ...agent.contact_map import ContactMap

UPDATE_CONTACT_MAPPING_DEFINITION = ToolDefinition(
    name="update_contact_mapping",
    description=(
        "Cache a sender-to-name mapping for future recognition. "
        "Call this after identifying an unknown sender via memory_search."
    ),
    parameters={
        "channel": ToolParameter(
            type="string",
            description="Channel name (e.g. 'gmail', 'line').",
        ),
        "sender_key": ToolParameter(
            type="string",
            description="Sender identifier on that channel (email address, display name, etc.).",
        ),
        "name": ToolParameter(
            type="string",
            description="Resolved person name from memory.",
        ),
    },
    required=["channel", "sender_key", "name"],
)


def create_update_contact_mapping(contact_map: ContactMap) -> Callable[..., str]:
    """Create an update_contact_mapping function bound to a ContactMap."""

    def update_contact_mapping(channel: str, sender_key: str, name: str) -> str:
        contact_map.update(channel, sender_key, name)
        return f"OK: {channel}/{sender_key} -> {name}"

    return update_contact_mapping
