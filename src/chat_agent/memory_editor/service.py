"""Memory editor service — deterministic apply with idempotency."""

from __future__ import annotations

from pathlib import Path

from .apply import apply_request
from .schema import (
    AppliedItem,
    ErrorItem,
    MemoryEditBatch,
    MemoryEditResult,
)
from .session_log import SessionCommitLog


class MemoryEditor:
    """Apply deterministic memory operations with idempotency tracking."""

    def __init__(self, *, commit_log: SessionCommitLog) -> None:
        self.commit_log = commit_log

    def apply_batch(
        self,
        batch: MemoryEditBatch,
        *,
        allowed_paths: list[str],
        base_dir: Path,
    ) -> MemoryEditResult:
        """Apply all requests with idempotency checks."""
        applied: list[AppliedItem] = []
        errors: list[ErrorItem] = []

        for req in batch.requests:
            payload_hash = req.payload_hash()
            if self.commit_log.is_applied(batch.turn_id, req.request_id, payload_hash):
                applied.append(
                    AppliedItem(
                        request_id=req.request_id,
                        status="already_applied",
                        path=req.target_path,
                    )
                )
                continue

            outcome = apply_request(
                req,
                allowed_paths=allowed_paths,
                base_dir=base_dir,
            )
            if outcome.status in {"applied", "noop"}:
                applied.append(
                    AppliedItem(
                        request_id=req.request_id,
                        status=outcome.status,
                        path=req.target_path,
                    )
                )
                self.commit_log.mark_applied(batch.turn_id, req.request_id, payload_hash)
            else:
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code=outcome.code or "apply_failed",
                        detail=outcome.detail or "unknown_failure",
                    )
                )

        return MemoryEditResult(
            status="ok" if not errors else "failed",
            turn_id=batch.turn_id,
            applied=applied,
            errors=errors,
        )
