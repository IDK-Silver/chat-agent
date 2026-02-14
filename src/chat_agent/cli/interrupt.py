"""ESC key interrupt monitor for cancelling LLM generation.

Runs a background daemon thread that switches the terminal to cbreak mode
and watches stdin for a standalone ESC keypress (\x1b not followed by an
escape sequence).  When detected, it sends SIGINT to the current process
so httpx / any blocking call raises KeyboardInterrupt.
"""

import os
import select
import signal
import sys
import termios
import threading
import tty


class EscInterruptMonitor:
    """Monitor stdin for standalone ESC and convert it to SIGINT."""

    def __init__(self) -> None:
        self._old_settings: list | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Enter cbreak mode and start the background monitor thread."""
        if not sys.stdin.isatty():
            return

        fd = sys.stdin.fileno()
        self._old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the monitor thread to exit and restore terminal settings."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

        if self._old_settings is not None:
            try:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings,
                )
            except Exception:
                pass
            self._old_settings = None

    def _monitor(self) -> None:
        """Background thread: watch stdin for standalone ESC."""
        fd = sys.stdin.fileno()

        while not self._stop_event.is_set():
            # Wait up to 100ms for input
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                continue

            try:
                ch = os.read(fd, 1)
            except OSError:
                break

            if ch != b"\x1b":
                continue

            # Got ESC byte - check if it's part of an escape sequence
            # (arrow keys, etc. send \x1b followed by more bytes within ~50ms)
            seq_ready, _, _ = select.select([fd], [], [], 0.05)
            if seq_ready:
                # More bytes followed -> escape sequence, drain and ignore
                try:
                    os.read(fd, 32)
                except OSError:
                    pass
                continue

            # Standalone ESC -> send SIGINT
            os.kill(os.getpid(), signal.SIGINT)
            break
