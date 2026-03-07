"""Interactive session picker for --resume."""

from rich.console import Console

from .schema import SessionMetadata

_STATUS_LABELS = {
    "active": "[ACTIVE]",
    "completed": "[DONE]",
    "exited": "[EXIT]",
    "refreshed": "[ROTATE]",
}


def pick_session(sessions: list[SessionMetadata]) -> SessionMetadata | None:
    """Display interactive picker for session selection.

    Returns the selected SessionMetadata, or None if cancelled or empty.
    """
    if not sessions:
        print("No sessions found.")
        return None

    console = Console()
    for idx, s in enumerate(sessions, start=1):
        label = _STATUS_LABELS.get(s.status, s.status)
        created = s.created_at.strftime("%Y-%m-%d %H:%M")
        console.print(
            f"[{idx}] {label:8s} {s.session_id}  {created}  ({s.message_count} msgs)",
            highlight=False,
        )

    console.print("Select session number (Enter to cancel):", highlight=False)
    try:
        raw = console.input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if not raw.isdigit():
        console.print("Invalid selection.", highlight=False)
        return None
    idx = int(raw) - 1
    if idx < 0 or idx >= len(sessions):
        console.print("Selection out of range.", highlight=False)
        return None
    return sessions[idx]
