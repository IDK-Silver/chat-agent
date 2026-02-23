"""schedule_action tool: agent can schedule future wake-up turns.

The agent calls this to create one-time reminders that fire at a
specified time.  Each scheduled action becomes an InboundMessage with
``not_before`` sitting in the queue's pending/ directory.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ...llm.schema import ToolDefinition, ToolParameter
from ...timezone_utils import parse_timezone_spec

if TYPE_CHECKING:
    from ...agent.queue import PersistentPriorityQueue

logger = logging.getLogger(__name__)

_SCHEDULED_TEMPLATE = (
    "[SCHEDULED]\n"
    "Reason: {reason}\n"
    "Scheduled at: {scheduled_at}\n\n"
    "Act on this reason. Use send_message to deliver messages."
)

SCHEDULE_ACTION_DEFINITION = ToolDefinition(
    name="schedule_action",
    description=(
        "Schedule a future wake-up turn. Use 'add' to create a reminder, "
        "'list' to see pending scheduled actions, 'remove' to cancel one. "
        "System heartbeats cannot be removed."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform: 'add', 'list', or 'remove'.",
            enum=["add", "list", "remove"],
        ),
        "reason": ToolParameter(
            type="string",
            description=(
                "Why this wake-up is needed (add only). "
                "This text will appear in the [SCHEDULED] message."
            ),
        ),
        "trigger_spec": ToolParameter(
            type="string",
            description=(
                "When to trigger (add only). "
                "ISO datetime in local time, e.g. '2026-02-22T09:00'."
            ),
        ),
        "pending_id": ToolParameter(
            type="string",
            description=(
                "Filename of the pending message to remove (remove only). "
                "Get this from the 'list' action."
            ),
        ),
    },
    required=["action"],
)


def create_schedule_action(
    queue: PersistentPriorityQueue,
    *,
    timezone_name: str = "UTC+8",
) -> Callable[..., str]:
    """Create a schedule_action function bound to a queue."""
    from ...agent.queue import _deserialize
    from ...agent.schema import InboundMessage

    tz = parse_timezone_spec(timezone_name)

    def schedule_action(
        action: str,
        reason: str | None = None,
        trigger_spec: str | None = None,
        pending_id: str | None = None,
    ) -> str:
        if action == "add":
            return _handle_add(reason, trigger_spec)
        if action == "list":
            return _handle_list()
        if action == "remove":
            return _handle_remove(pending_id)
        return f"Error: unknown action '{action}'"

    def _handle_add(reason: str | None, trigger_spec: str | None) -> str:
        if not reason:
            return "Error: 'reason' is required for add"
        if not trigger_spec:
            return "Error: 'trigger_spec' is required for add"

        try:
            local_dt = datetime.fromisoformat(trigger_spec)
        except ValueError:
            return f"Error: invalid datetime format: {trigger_spec!r}"

        # Interpret as local time if naive, then convert to UTC
        if local_dt.tzinfo is None:
            local_dt = local_dt.replace(tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)

        now = datetime.now(timezone.utc)
        if utc_dt <= now:
            return "Error: trigger_spec must be in the future"

        display_time = local_dt.strftime("%Y-%m-%d %H:%M")
        content = _SCHEDULED_TEMPLATE.format(
            reason=reason,
            scheduled_at=display_time,
        )
        msg = InboundMessage(
            channel="system",
            content=content,
            priority=0,
            sender="system",
            metadata={"scheduled_reason": reason},
            not_before=utc_dt,
        )
        queue.put(msg)
        delta = utc_dt - now
        hours = delta.total_seconds() / 3600
        logger.info("Scheduled action: %s at %s", reason, display_time)
        return f"OK: scheduled at {display_time} ({hours:.1f}h from now)"

    def _handle_list() -> str:
        items = queue.scan_pending(channel="system")
        if not items:
            return "No pending scheduled actions."

        lines = []
        for filepath, msg in items:
            nb = msg.not_before
            if nb is not None:
                # Display in local time
                if nb.tzinfo is None:
                    nb = nb.replace(tzinfo=timezone.utc)
                nb_str = nb.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            else:
                nb_str = "immediate"
            is_system = msg.metadata.get("system", False)
            tag = " [system]" if is_system else ""
            preview = msg.content[:80].replace("\n", " ")
            lines.append(f"- {filepath.name}{tag} (at {nb_str}): {preview}")
        return "\n".join(lines)

    def _handle_remove(pending_id: str | None) -> str:
        if not pending_id:
            return "Error: 'pending_id' is required for remove"

        filepath = queue._pending_dir / pending_id
        if not filepath.exists():
            return f"Error: pending message not found: {pending_id}"

        # Check if it's a system heartbeat
        try:
            data = json.loads(filepath.read_text())
            msg = _deserialize(data)
            if msg.metadata.get("system"):
                return "Error: cannot remove system heartbeats"
        except Exception:
            return "Error: failed to read pending message"

        queue.remove_pending(filepath)
        return f"OK: removed {pending_id}"

    return schedule_action
