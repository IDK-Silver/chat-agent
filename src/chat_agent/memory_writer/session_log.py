"""In-memory idempotency log for memory writer."""

from __future__ import annotations


class SessionCommitLog:
    """Track applied (turn_id, request_id, payload_hash) tuples for this process."""

    def __init__(self) -> None:
        self._applied: set[tuple[str, str, str]] = set()

    def is_applied(self, turn_id: str, request_id: str, payload_hash: str) -> bool:
        """Check whether a request has already been applied."""
        return (turn_id, request_id, payload_hash) in self._applied

    def mark_applied(self, turn_id: str, request_id: str, payload_hash: str) -> None:
        """Mark request as applied."""
        self._applied.add((turn_id, request_id, payload_hash))

