"""agent_note tool: structured key-value state tracking.

The agent uses this to create, update, list, and remove notes that
track real-time user state.  Each note can have trigger phrases that
cause the system to prompt the agent to review the note when matching
text appears in a user message.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...llm.schema import ToolDefinition, ToolParameter

if TYPE_CHECKING:
    from ...agent.note_store import NoteStore

logger = logging.getLogger(__name__)

AGENT_NOTE_DEFINITION = ToolDefinition(
    name="agent_note",
    description=(
        "Manage structured notes for tracking user state (location, schedule, etc.). "
        "'create' adds a note with optional trigger phrases, "
        "'update' changes its value or triggers, "
        "'list' shows all notes, "
        "'remove' deletes a note. "
        "Triggers are phrases that, when found in a user message, prompt you to "
        "review and update the note."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform.",
            enum=["create", "update", "list", "remove"],
        ),
        "key": ToolParameter(
            type="string",
            description="Note key (required for create/update/remove). Use short, descriptive keys like 'location', 'mood', 'schedule_today'.",
        ),
        "value": ToolParameter(
            type="string",
            description="Note value (required for create, optional for update).",
        ),
        "triggers": ToolParameter(
            type="array",
            description=(
                "Trigger phrases (optional). When a user message contains any of "
                "these substrings, you'll be prompted to review and update this note. "
                "Example: [\"arrived\", \"got home\", \"heading out\"]"
            ),
            items={"type": "string"},
        ),
        "description": ToolParameter(
            type="string",
            description="Optional description of what this note tracks.",
        ),
    },
    required=["action"],
)


def create_agent_note(
    note_store: NoteStore,
) -> Callable[..., str]:
    """Create an agent_note function bound to a note store."""

    def agent_note(
        action: str,
        key: str | None = None,
        value: str | None = None,
        triggers: list[str] | None = None,
        description: str | None = None,
    ) -> str:
        if action == "create":
            return _handle_create(key, value, triggers, description)
        if action == "update":
            return _handle_update(key, value, triggers, description)
        if action == "list":
            return _handle_list()
        if action == "remove":
            return _handle_remove(key)
        return f"Error: unknown action '{action}'"

    def _handle_create(
        key: str | None,
        value: str | None,
        triggers: list[str] | None,
        description: str | None,
    ) -> str:
        if not key:
            return "Error: 'key' is required for create"
        if value is None:
            return "Error: 'value' is required for create"

        result = note_store.create(
            key=key,
            value=value,
            triggers=triggers,
            description=description,
        )
        if isinstance(result, str):
            return result
        parts = [f"OK: created note '{key}'"]
        if result.triggers:
            parts.append(f"triggers: {result.triggers}")
        return " | ".join(parts)

    def _handle_update(
        key: str | None,
        value: str | None,
        triggers: list[str] | None,
        description: str | None,
    ) -> str:
        if not key:
            return "Error: 'key' is required for update"

        note = note_store.update(
            key=key,
            value=value,
            triggers=triggers,
            description=description,
        )
        if note is None:
            return f"Error: note '{key}' not found"
        return f"OK: updated note '{key}'"

    def _handle_list() -> str:
        return note_store.format_list_detail()

    def _handle_remove(key: str | None) -> str:
        if not key:
            return "Error: 'key' is required for remove"
        if not note_store.remove(key):
            return f"Error: note '{key}' not found"
        return f"OK: removed note '{key}'"

    return agent_note
