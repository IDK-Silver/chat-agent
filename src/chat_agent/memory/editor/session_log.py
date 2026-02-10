"""In-memory idempotency log for memory writer."""

from __future__ import annotations

from threading import Lock


class SessionCommitLog:
    """Track applied (turn_id, request_id, payload_hash) tuples for this process."""

    def __init__(self) -> None:
        self._applied: set[tuple[str, str, str]] = set()
        self._lock = Lock()

    def is_applied(self, turn_id: str, request_id: str, payload_hash: str) -> bool:
        """Check whether a request has already been applied."""
        with self._lock:
            return (turn_id, request_id, payload_hash) in self._applied

    def mark_applied(self, turn_id: str, request_id: str, payload_hash: str) -> None:
        """Mark request as applied."""
        with self._lock:
            self._applied.add((turn_id, request_id, payload_hash))
