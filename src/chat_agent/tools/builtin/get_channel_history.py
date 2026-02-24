"""Channel history lookup tool (generic interface, Discord backend first)."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import TYPE_CHECKING

from ...llm.schema import ToolDefinition, ToolParameter

if TYPE_CHECKING:
    from ...agent.contact_map import ContactMap
    from ...agent.discord_history import DiscordHistoryStore
    from ...agent.turn_context import TurnContext


GET_CHANNEL_HISTORY_DEFINITION = ToolDefinition(
    name="get_channel_history",
    description=(
        "Query recent message history for a channel using a generic interface. "
        "Currently only supports channel='discord'."
    ),
    parameters={
        "channel": ToolParameter(
            type="string",
            description="Channel backend name. Generic interface; v1 supports only 'discord'.",
        ),
        "to": ToolParameter(
            type="string",
            description=(
                "Target alias to resolve via ContactMap (e.g. person name or '#channel @ guild'). "
                "Optional if querying current Discord channel."
            ),
        ),
        "channel_id": ToolParameter(
            type="string",
            description="Explicit channel id. Takes precedence over 'to'.",
        ),
        "limit": ToolParameter(
            type="integer",
            description="Max messages to return after folding edits. Default 50.",
        ),
        "since_minutes": ToolParameter(
            type="integer",
            description="Only include messages newer than this many minutes.",
        ),
    },
    required=["channel"],
)


def create_get_channel_history(
    history_store: "DiscordHistoryStore",
    contact_map: "ContactMap",
    turn_context: "TurnContext",
) -> Callable[..., str]:
    """Create get_channel_history bound to runtime state."""

    def get_channel_history(
        channel: str,
        to: str | None = None,
        channel_id: str | None = None,
        limit: int = 50,
        since_minutes: int | None = None,
    ) -> str:
        if channel != "discord":
            return "Error: get_channel_history currently supports only 'discord'"

        target = to
        resolved_channel_id = channel_id
        if resolved_channel_id is None and to:
            resolved = contact_map.reverse_lookup("discord", to)
            if resolved is not None:
                resolved_channel_id = resolved
                # Person aliases resolve to Discord user IDs. For history lookup we need the DM channel ID.
                if history_store.get_channel_entry(resolved_channel_id) is None:
                    for entry in history_store.list_registered_channels():
                        if not isinstance(entry, dict):
                            continue
                        if str(entry.get("dm_peer_user_id") or "") == resolved_channel_id:
                            candidate = str(entry.get("channel_id") or "").strip()
                            if candidate:
                                resolved_channel_id = candidate
                                break
            if resolved_channel_id is None:
                return (
                    f"Error: no discord channel/contact mapping found for '{to}'. "
                    "Use a known alias or provide channel_id."
                )
        if resolved_channel_id is None:
            if (
                turn_context.channel == "discord"
                and isinstance(turn_context.metadata, dict)
            ):
                current = turn_context.metadata.get("channel_id")
                if isinstance(current, str) and current:
                    resolved_channel_id = current
                    if target is None:
                        target = turn_context.sender
        if resolved_channel_id is None:
            return "Error: provide 'to' or 'channel_id' (or call from a Discord turn)"

        try:
            limit_i = int(limit)
        except (TypeError, ValueError):
            return "Error: limit must be an integer"
        if limit_i < 0:
            return "Error: limit must be >= 0"

        since_i: int | None = None
        if since_minutes is not None:
            try:
                since_i = int(since_minutes)
            except (TypeError, ValueError):
                return "Error: since_minutes must be an integer"
            if since_i < 0:
                return "Error: since_minutes must be >= 0"

        payload = history_store.get_channel_history(
            resolved_channel_id,
            limit=limit_i,
            since_minutes=since_i,
            target=target or resolved_channel_id,
        )
        return json.dumps(payload, ensure_ascii=False)

    return get_channel_history
