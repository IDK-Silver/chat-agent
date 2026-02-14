"""Interactive terminal picker for single-item selection."""

import os
import select
import sys
import termios
import tty


def pick_one(items: list[str], *, title: str = "") -> int | None:
    """Display an interactive picker and return the selected index.

    Arrow keys to navigate (wraps around), Enter to confirm, ESC to cancel.
    Cleans up all drawn lines on exit so the terminal is left pristine.

    Returns:
        Selected index (0-based), or None if cancelled or empty list.
    """
    if not items:
        return None

    if not sys.stdin.isatty():
        return None

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    cursor = 0
    total_lines = len(items) + (1 if title else 0)

    try:
        tty.setcbreak(fd)
        # Hide cursor during selection
        sys.stdout.write("\033[?25l")
        _draw(items, cursor, title)
        sys.stdout.flush()

        while True:
            key = _read_key(fd)
            if key is None:
                continue
            if key == "enter":
                _erase(total_lines)
                return cursor
            if key == "esc":
                _erase(total_lines)
                return None
            if key == "up":
                cursor = (cursor - 1) % len(items)
                _redraw(items, cursor, title, total_lines)
            elif key == "down":
                cursor = (cursor + 1) % len(items)
                _redraw(items, cursor, title, total_lines)
    finally:
        # Restore cursor visibility and terminal settings
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass


def _draw(items: list[str], cursor: int, title: str) -> None:
    """Draw the picker menu."""
    lines: list[str] = []
    if title:
        lines.append(f"  {title}")
    for i, item in enumerate(items):
        if i == cursor:
            # Bold cyan highlight with pointer
            lines.append(f"  \033[1;36m> {item}\033[0m")
        else:
            lines.append(f"    {item}")
    sys.stdout.write("\n".join(lines))


def _redraw(items: list[str], cursor: int, title: str, total_lines: int) -> None:
    """Move up to the start of the menu and redraw."""
    # Move cursor up to the first line
    if total_lines > 1:
        sys.stdout.write(f"\033[{total_lines - 1}A")
    # Return to column 0
    sys.stdout.write("\r")
    # Clear and redraw each line
    lines: list[str] = []
    if title:
        lines.append(f"\033[K  {title}")
    for i, item in enumerate(items):
        if i == cursor:
            lines.append(f"\033[K  \033[1;36m> {item}\033[0m")
        else:
            lines.append(f"\033[K    {item}")
    sys.stdout.write("\n".join(lines))
    sys.stdout.flush()


def _erase(total_lines: int) -> None:
    """Erase the menu area completely."""
    # Move cursor up to the first line
    if total_lines > 1:
        sys.stdout.write(f"\033[{total_lines - 1}A")
    sys.stdout.write("\r")
    # Clear each line
    for i in range(total_lines):
        sys.stdout.write("\033[K")
        if i < total_lines - 1:
            sys.stdout.write("\n")
    # Move back to the first line
    if total_lines > 1:
        sys.stdout.write(f"\033[{total_lines - 1}A")
    sys.stdout.write("\r")
    sys.stdout.flush()


def _read_key(fd: int) -> str | None:
    """Read a keypress from fd. Returns 'enter', 'esc', 'up', 'down', or None."""
    ready, _, _ = select.select([fd], [], [], 0.1)
    if not ready:
        return None

    try:
        ch = os.read(fd, 1)
    except OSError:
        return None

    if ch in (b"\r", b"\n"):
        return "enter"

    if ch == b"\x1b":
        # Check for escape sequence (arrow keys send \x1b[A etc.)
        seq_ready, _, _ = select.select([fd], [], [], 0.05)
        if not seq_ready:
            return "esc"
        try:
            seq = os.read(fd, 2)
        except OSError:
            return "esc"
        if seq == b"[A":
            return "up"
        if seq == b"[B":
            return "down"
        # Unknown sequence, ignore
        return None

    return None
