"""Interactive session picker for --resume."""

from ..cli.picker import pick_one
from .schema import SessionMetadata

_STATUS_LABELS = {
    "active": "[ACTIVE]",
    "completed": "[DONE]",
    "exited": "[EXIT]",
}


def pick_session(sessions: list[SessionMetadata]) -> SessionMetadata | None:
    """Display interactive picker for session selection.

    Returns the selected SessionMetadata, or None if cancelled or empty.
    """
    if not sessions:
        print("No sessions found.")
        return None

    items = []
    for s in sessions:
        label = _STATUS_LABELS.get(s.status, s.status)
        created = s.created_at.strftime("%Y-%m-%d %H:%M")
        items.append(f"{label:8s} {s.session_id}  {created}  ({s.message_count} msgs)")

    choice = pick_one(items, title="Recent sessions:")
    if choice is None:
        return None
    return sessions[choice]
