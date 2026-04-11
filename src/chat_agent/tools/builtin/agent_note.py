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
        "source_app": ToolParameter(
            type="string",
            description="Optional external source namespace, e.g. 'calendar' or 'reminders'.",
        ),
        "source_id": ToolParameter(
            type="string",
            description="Optional external source item id, such as an event uid or reminder id.",
        ),
        "source_label": ToolParameter(
            type="string",
            description="Optional source label for summary notes, e.g. 'next_event' or 'today_focus'.",
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
        source_app: str | None = None,
        source_id: str | None = None,
        source_label: str | None = None,
    ) -> str:
        source_error = _validate_source_fields(
            source_app=source_app,
            source_id=source_id,
            source_label=source_label,
        )
        if source_error:
            return source_error
        if action == "create":
            return _handle_create(
                key,
                value,
                triggers,
                description,
                source_app,
                source_id,
                source_label,
            )
        if action == "update":
            return _handle_update(
                key,
                value,
                triggers,
                description,
                source_app,
                source_id,
                source_label,
            )
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
        source_app: str | None,
        source_id: str | None,
        source_label: str | None,
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
            source_app=source_app,
            source_id=source_id,
            source_label=source_label,
        )
        if isinstance(result, str):
            return result
        parts = [f"OK: created note '{key}'"]
        source = _format_source_result(result.source_app, result.source_label, result.source_id)
        if source:
            parts.append(f"source: {source}")
        if result.triggers:
            parts.append(f"triggers: {result.triggers}")
        return " | ".join(parts)

    def _handle_update(
        key: str | None,
        value: str | None,
        triggers: list[str] | None,
        description: str | None,
        source_app: str | None,
        source_id: str | None,
        source_label: str | None,
    ) -> str:
        if not key:
            return "Error: 'key' is required for update"

        note = note_store.update(
            key=key,
            value=value,
            triggers=triggers,
            description=description,
            source_app=source_app,
            source_id=source_id,
            source_label=source_label,
        )
        if note is None:
            return f"Error: note '{key}' not found"
        source = _format_source_result(note.source_app, note.source_label, note.source_id)
        if source:
            return f"OK: updated note '{key}' | source: {source}"
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


def _validate_source_fields(
    *,
    source_app: str | None,
    source_id: str | None,
    source_label: str | None,
) -> str | None:
    if (source_id or source_label) and not source_app:
        return "Error: 'source_app' is required when source_id or source_label is set"
    return None


def _format_source_result(
    source_app: str | None,
    source_label: str | None,
    source_id: str | None,
) -> str | None:
    if not source_app:
        return None
    text = source_app
    if source_label:
        text = f"{text}:{source_label}"
    if source_id:
        text = f"{text} ({source_id})"
    return text
