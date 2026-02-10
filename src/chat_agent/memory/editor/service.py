"""Memory editor service — instruction planning + deterministic apply."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .apply import apply_operation, resolve_memory_path
from .planner import MemoryEditPlanner
from .schema import (
    AppliedItem,
    ErrorItem,
    MemoryEditBatch,
    MemoryEditOperation,
    MemoryEditResult,
)
from .session_log import SessionCommitLog


class MemoryEditor:
    """Plan and apply memory operations with idempotency tracking."""

    def __init__(
        self,
        *,
        commit_log: SessionCommitLog,
        planner: MemoryEditPlanner,
    ) -> None:
        self.commit_log = commit_log
        self.planner = planner

    def apply_batch(
        self,
        batch: MemoryEditBatch,
        *,
        allowed_paths: list[str],
        base_dir: Path,
    ) -> MemoryEditResult:
        """Apply all instruction requests with planning and idempotency checks."""
        applied: list[AppliedItem] = []
        errors: list[ErrorItem] = []

        for req in batch.requests:
            try:
                target = resolve_memory_path(
                    req.target_path,
                    allowed_paths=allowed_paths,
                    base_dir=base_dir,
                )
            except ValueError as e:
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code="path_invalid",
                        detail=str(e),
                    )
                )
                continue

            if target.exists() and not target.is_file():
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code="not_a_file",
                        detail=str(target),
                    )
                )
                continue

            file_exists = target.exists()
            file_content = (
                target.read_text(encoding="utf-8")
                if file_exists
                else ""
            )
            plan = self.planner.plan(
                request=req,
                as_of=batch.as_of,
                turn_id=batch.turn_id,
                file_exists=file_exists,
                file_content=file_content,
            )
            if plan.status != "ok":
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code=plan.error_code or "instruction_not_actionable",
                        detail=plan.error_detail or req.instruction,
                    )
                )
                continue

            payload_hash = _operations_hash(plan.operations)
            if self.commit_log.is_applied(batch.turn_id, req.request_id, payload_hash):
                applied.append(
                    AppliedItem(
                        request_id=req.request_id,
                        status="already_applied",
                        path=req.target_path,
                    )
                )
                continue

            request_changed = False
            failed_outcome = None
            for operation in plan.operations:
                outcome = apply_operation(
                    target,
                    operation,
                    base_dir=base_dir,
                )
                if outcome.status == "error":
                    failed_outcome = outcome
                    break
                if outcome.status == "applied":
                    request_changed = True

            if failed_outcome is not None:
                _rollback_request_file(
                    target=target,
                    existed=file_exists,
                    original_content=file_content,
                )
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code=failed_outcome.code or "apply_failed",
                        detail=failed_outcome.detail or "unknown_failure",
                    )
                )
                continue

            applied.append(
                AppliedItem(
                    request_id=req.request_id,
                    status="applied" if request_changed else "noop",
                    path=req.target_path,
                )
            )
            self.commit_log.mark_applied(batch.turn_id, req.request_id, payload_hash)

        return MemoryEditResult(
            status="ok" if not errors else "failed",
            turn_id=batch.turn_id,
            applied=applied,
            errors=errors,
        )


def _operations_hash(operations: list[MemoryEditOperation]) -> str:
    """Build stable hash from planner-produced operations."""
    payload = json.dumps(
        [
            op.model_dump(mode="json", exclude_none=True)
            for op in operations
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rollback_request_file(
    *,
    target: Path,
    existed: bool,
    original_content: str,
) -> None:
    """Rollback one request's target file to its original state."""
    if existed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original_content, encoding="utf-8")
        return

    if target.exists() and target.is_file():
        target.unlink()
