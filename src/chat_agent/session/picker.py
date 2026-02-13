"""Interactive session picker for --resume."""

from .schema import SessionMetadata

_STATUS_LABELS = {
    "active": "[ACTIVE]",
    "completed": "[DONE]",
    "exited": "[EXIT]",
}


def pick_session(sessions: list[SessionMetadata]) -> SessionMetadata | None:
    """Display numbered list of sessions and let user pick one.

    Returns the selected SessionMetadata, or None if cancelled.
    """
    if not sessions:
        print("No sessions found.")
        return None

    print("\nRecent sessions:")
    for i, s in enumerate(sessions, 1):
        label = _STATUS_LABELS.get(s.status, s.status)
        created = s.created_at.strftime("%Y-%m-%d %H:%M")
        print(f"  {i:3d}. {label:8s} {s.session_id}  {created}  ({s.message_count} msgs)")
    print()

    try:
        raw = input("Select session number (or Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not raw:
        return None

    try:
        idx = int(raw) - 1
    except ValueError:
        print(f"Invalid input: {raw}")
        return None

    if idx < 0 or idx >= len(sessions):
        print(f"Out of range: {raw}")
        return None

    return sessions[idx]
