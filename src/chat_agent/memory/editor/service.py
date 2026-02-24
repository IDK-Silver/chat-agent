"""Memory editor service — instruction planning + deterministic apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import logging
import os
from pathlib import Path

from ...core.schema import MemoryEditWarningsConfig
from ...workspace.people import sync_people_index_entry
from ..index_kind import IndexKind, classify_memory_index_path, is_registry_index_path
from .apply import (
    apply_operation,
    delete_index_for_cleanup,
    remove_index_link,
    resolve_memory_path,
    _ensure_index_link,
)
from .planner import MemoryEditPlanner
from .schema import (
    AppliedItem,
    ErrorItem,
    MemoryEditBatch,
    MemoryEditOperation,
    MemoryEditPlan,
    MemoryEditRequest,
    MemoryEditResult,
    WarningItem,
)
from .session_log import SessionCommitLog

logger = logging.getLogger(__name__)

_MAX_PARALLEL_TARGET_FILES = max(1, min(8, os.cpu_count() or 1))

_WARNING_DUPLICATE_THRESHOLD = 0.7


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
        warnings_config: MemoryEditWarningsConfig | None = None,
    ) -> None:
        self.commit_log = commit_log
        self.planner = planner
        self.warnings_config = warnings_config or MemoryEditWarningsConfig()

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
        all_warnings: list[WarningItem] = []
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

        # Post-batch warnings: check applied targets for file health
        warned_paths: set[str] = set()
        for item in applied:
            if item.status != "applied" or item.path in warned_paths:
                continue
            try:
                target = resolve_memory_path(
                    item.path, allowed_paths=allowed_paths, base_dir=base_dir,
                )
            except ValueError:
                continue
            # Find what operations were planned for this target
            target_reqs = requests_by_target.get(target, [])
            had_append = any(
                req.request.instruction for req in target_reqs
            )
            if had_append:
                ws = _check_file_warnings(target, item.path, self.warnings_config)
                all_warnings.extend(ws)
                warned_paths.add(item.path)

        _sync_people_registry_after_batch(
            applied=applied,
            base_dir=base_dir,
            as_of=batch.as_of,
        )

        return MemoryEditResult(
            status="ok" if not errors else "failed",
            turn_id=batch.turn_id,
            applied=applied,
            errors=errors,
            warnings=all_warnings,
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

        # Post-apply: auto-maintain parent index.md
        if request_changed:
            _auto_maintain_index(
                target=target,
                plan=plan,
                instruction=req.instruction,
                base_dir=base_dir,
            )

        return AppliedItem(
            request_id=req.request_id,
            status="applied" if request_changed else "noop",
            path=req.target_path,
        )


def _sync_people_registry_after_batch(
    *,
    applied: list[AppliedItem],
    base_dir: Path,
    as_of: str,
) -> None:
    """Sync memory/people/index.md for any user directories touched in this batch."""
    user_ids: set[str] = set()
    seen_date = _coerce_as_of_date(as_of)

    for item in applied:
        if item.status != "applied":
            continue
        if is_registry_index_path(item.path):
            continue
        user_id = _people_user_id_from_memory_path(item.path)
        if user_id is not None:
            user_ids.add(user_id)

    if not user_ids:
        return

    memory_dir = base_dir / "memory"
    for user_id in sorted(user_ids):
        try:
            sync_people_index_entry(memory_dir, user_id, seen_date=seen_date)
        except Exception:
            logger.warning("Failed to sync people index for user_id=%s", user_id, exc_info=True)


def _people_user_id_from_memory_path(path: str) -> str | None:
    normalized = str(path).strip().replace("\\", "/")
    parts = normalized.split("/")
    if len(parts) < 4:
        return None
    if parts[0] != "memory" or parts[1] != "people":
        return None
    user_id = parts[2].strip()
    if not user_id or user_id == "index.md":
        return None
    return user_id


def _coerce_as_of_date(as_of: str) -> str:
    candidate = (as_of or "")[:10]
    try:
        date.fromisoformat(candidate)
        return candidate
    except ValueError:
        return date.today().isoformat()


def _to_memory_rel_path(path: Path, *, base_dir: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return None
    return rel.as_posix()


# -- Index auto-maintenance ----------------------------------------------------

def _auto_maintain_index(
    *,
    target: Path,
    plan: MemoryEditPlan,
    instruction: str,
    base_dir: Path,
) -> None:
    """Auto-add/remove index links after create/delete operations."""
    if target.name == "index.md":
        return

    parent_index = target.parent / "index.md"
    parent_rel = _to_memory_rel_path(parent_index, base_dir=base_dir)
    if parent_rel is not None and classify_memory_index_path(parent_rel) == IndexKind.REGISTRY:
        # Registry indexes are domain-owned (e.g. people/index.md table).
        return

    for op in plan.operations:
        if op.kind == "create_if_missing":
            # Extract description from instruction (truncate to ~80 chars)
            desc = instruction[:80].rstrip()
            _ensure_index_link(
                parent_index,
                link_path=target.name,
                link_title=f"{target.name} — {desc}",
                base_dir=base_dir,
            )

        elif op.kind == "delete_file":
            # Remove link from parent index
            remove_index_link(parent_index, target.name)

            # Check if directory is now empty (only index.md remains)
            _cleanup_empty_directory(target.parent)


def _cleanup_empty_directory(directory: Path) -> None:
    """Remove empty directory's index.md and its parent link."""
    if not directory.is_dir():
        return

    remaining_md = [
        f for f in directory.iterdir()
        if f.suffix == ".md" and f.name != "index.md"
    ]
    if remaining_md:
        return

    # Directory only has index.md (or nothing) — clean up
    index_file = directory / "index.md"
    if delete_index_for_cleanup(index_file):
        # Also remove this directory's link from grandparent index
        grandparent_index = directory.parent / "index.md"
        dir_name = directory.name
        remove_index_link(grandparent_index, f"{dir_name}/")
        remove_index_link(grandparent_index, dir_name)
        logger.info("Cleaned up empty directory index: %s", directory)


# -- File health warnings ------------------------------------------------------

def _check_file_warnings(
    target: Path,
    rel_path: str,
    config: MemoryEditWarningsConfig,
) -> list[WarningItem]:
    """Check file state and return non-blocking warnings."""
    if not target.is_file():
        return []

    # Check ignore list: match filename or directory pattern
    for pattern in config.ignore:
        if pattern.endswith("/"):
            if f"/{pattern}" in f"/{rel_path}" or rel_path.startswith(pattern):
                return []
        elif target.name == pattern:
            return []

    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    warnings: list[WarningItem] = []

    if len(lines) > config.max_lines:
        warnings.append(WarningItem(
            path=rel_path,
            code="file_too_long",
            detail=(
                f"{len(lines)} lines (threshold: {config.max_lines}), "
                "see skills/memory-maintenance/ or ask user"
            ),
        ))

    dupes = _find_duplicate_lines(lines)
    if dupes:
        near = ", ".join(str(n) for n in dupes[:3])
        warnings.append(WarningItem(
            path=rel_path,
            code="possible_duplicates",
            detail=(
                f"similar lines near lines {near}, "
                "see skills/memory-maintenance/ or ask user"
            ),
        ))

    return warnings


def _find_duplicate_lines(lines: list[str]) -> list[int]:
    """Find line numbers with high token overlap with their neighbors."""
    duplicates: list[int] = []
    prev_tokens: set[str] | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            prev_tokens = None
            continue

        tokens = set(stripped.split())
        if len(tokens) < 3:
            prev_tokens = tokens
            continue

        if prev_tokens and len(prev_tokens) >= 3:
            intersection = tokens & prev_tokens
            union = tokens | prev_tokens
            if union and len(intersection) / len(union) > _WARNING_DUPLICATE_THRESHOLD:
                duplicates.append(i + 1)  # 1-indexed

        prev_tokens = tokens

    return duplicates


# -- Helpers -------------------------------------------------------------------

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
