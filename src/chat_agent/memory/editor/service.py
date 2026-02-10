"""Memory editor service — instruction planning + deterministic apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from .apply import apply_operation, resolve_memory_path
from .planner import MemoryEditPlanner
from .schema import (
    AppliedItem,
    ErrorItem,
    MemoryEditBatch,
    MemoryEditOperation,
    MemoryEditRequest,
    MemoryEditResult,
)
from .session_log import SessionCommitLog

_MAX_PARALLEL_TARGET_FILES = max(1, min(8, os.cpu_count() or 1))


@dataclass
class _IndexedRequest:
    """One request plus its original batch index."""

    index: int
    request: MemoryEditRequest


@dataclass
class _IndexedOutcome:
    """Apply result keyed by original request index."""

    index: int
    applied: AppliedItem | None = None
    error: ErrorItem | None = None


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
        """Apply all requests, parallelized across distinct target files."""
        indexed_outcomes: dict[int, _IndexedOutcome] = {}
        requests_by_target: dict[Path, list[_IndexedRequest]] = {}

        for index, req in enumerate(batch.requests):
            try:
                target = resolve_memory_path(
                    req.target_path,
                    allowed_paths=allowed_paths,
                    base_dir=base_dir,
                )
            except ValueError as e:
                indexed_outcomes[index] = _IndexedOutcome(
                    index=index,
                    error=ErrorItem(
                        request_id=req.request_id,
                        code="path_invalid",
                        detail=str(e),
                    ),
                )
                continue

            if target.exists() and not target.is_file():
                indexed_outcomes[index] = _IndexedOutcome(
                    index=index,
                    error=ErrorItem(
                        request_id=req.request_id,
                        code="not_a_file",
                        detail=str(target),
                    ),
                )
                continue

            requests_by_target.setdefault(target, []).append(
                _IndexedRequest(index=index, request=req)
            )

        grouped_outcomes = self._apply_grouped_requests(
            batch=batch,
            requests_by_target=requests_by_target,
            base_dir=base_dir,
        )
        indexed_outcomes.update(grouped_outcomes)

        applied: list[AppliedItem] = []
        errors: list[ErrorItem] = []
        for index, req in enumerate(batch.requests):
            outcome = indexed_outcomes.get(index)
            if outcome is None:
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code="internal_error",
                        detail="missing_request_outcome",
                    )
                )
                continue
            if outcome.applied is not None:
                applied.append(outcome.applied)
                continue
            if outcome.error is not None:
                errors.append(outcome.error)
                continue
            errors.append(
                ErrorItem(
                    request_id=req.request_id,
                    code="internal_error",
                    detail="invalid_request_outcome",
                )
            )

        return MemoryEditResult(
            status="ok" if not errors else "failed",
            turn_id=batch.turn_id,
            applied=applied,
            errors=errors,
        )

    def _apply_grouped_requests(
        self,
        *,
        batch: MemoryEditBatch,
        requests_by_target: dict[Path, list[_IndexedRequest]],
        base_dir: Path,
    ) -> dict[int, _IndexedOutcome]:
        """Apply grouped requests; each target file is processed in isolation."""
        if not requests_by_target:
            return {}

        groups = list(requests_by_target.items())
        max_workers = min(len(groups), _MAX_PARALLEL_TARGET_FILES)
        if max_workers <= 1:
            outcomes: dict[int, _IndexedOutcome] = {}
            for target, indexed_requests in groups:
                for outcome in self._apply_target_requests(
                    batch=batch,
                    target=target,
                    indexed_requests=indexed_requests,
                    base_dir=base_dir,
                ):
                    outcomes[outcome.index] = outcome
            return outcomes

        outcomes = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self._apply_target_requests,
                    batch=batch,
                    target=target,
                    indexed_requests=indexed_requests,
                    base_dir=base_dir,
                )
                for target, indexed_requests in groups
            ]
            for future in futures:
                for outcome in future.result():
                    outcomes[outcome.index] = outcome
        return outcomes

    def _apply_target_requests(
        self,
        *,
        batch: MemoryEditBatch,
        target: Path,
        indexed_requests: list[_IndexedRequest],
        base_dir: Path,
    ) -> list[_IndexedOutcome]:
        """Apply one target file's requests sequentially to preserve file ordering."""
        outcomes: list[_IndexedOutcome] = []
        for indexed in indexed_requests:
            request_outcome = self._apply_one_request(
                batch=batch,
                target=target,
                req=indexed.request,
                base_dir=base_dir,
            )
            outcomes.append(
                _IndexedOutcome(
                    index=indexed.index,
                    applied=request_outcome if isinstance(request_outcome, AppliedItem) else None,
                    error=request_outcome if isinstance(request_outcome, ErrorItem) else None,
                )
            )
        return outcomes

    def _apply_one_request(
        self,
        *,
        batch: MemoryEditBatch,
        target: Path,
        req: MemoryEditRequest,
        base_dir: Path,
    ) -> AppliedItem | ErrorItem:
        """Apply one request end-to-end with request-level rollback on failure."""
        if target.exists() and not target.is_file():
            return ErrorItem(
                request_id=req.request_id,
                code="not_a_file",
                detail=str(target),
            )

        file_exists = target.exists()
        file_content = target.read_text(encoding="utf-8") if file_exists else ""
        plan = self.planner.plan(
            request=req,
            as_of=batch.as_of,
            turn_id=batch.turn_id,
            file_exists=file_exists,
            file_content=file_content,
        )
        if plan.status != "ok":
            return ErrorItem(
                request_id=req.request_id,
                code=plan.error_code or "instruction_not_actionable",
                detail=plan.error_detail or req.instruction,
            )

        payload_hash = _operations_hash(plan.operations)
        if self.commit_log.is_applied(batch.turn_id, req.request_id, payload_hash):
            return AppliedItem(
                request_id=req.request_id,
                status="already_applied",
                path=req.target_path,
            )

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
            return ErrorItem(
                request_id=req.request_id,
                code=failed_outcome.code or "apply_failed",
                detail=failed_outcome.detail or "unknown_failure",
            )

        self.commit_log.mark_applied(batch.turn_id, req.request_id, payload_hash)
        return AppliedItem(
            request_id=req.request_id,
            status="applied" if request_changed else "noop",
            path=req.target_path,
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
