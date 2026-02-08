"""Memory writer service backed by dedicated writer LLM."""

from __future__ import annotations

import json
from pathlib import Path

from ..llm.base import LLMClient
from ..llm.schema import Message
from ..reviewer.json_extract import extract_json_object
from .apply import apply_request, resolve_memory_path
from .schema import (
    AppliedItem,
    ErrorItem,
    MemoryEditBatch,
    MemoryEditResult,
    WriterDecision,
)
from .session_log import SessionCommitLog


class MemoryWriter:
    """Run writer-model checks, then apply deterministic memory operations."""

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        parse_retry_prompt: str,
        *,
        parse_retries: int,
        max_retries: int,
        commit_log: SessionCommitLog,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.parse_retry_prompt = parse_retry_prompt
        self.parse_retries = max(0, parse_retries)
        self.max_retries = max(0, max_retries)
        self.commit_log = commit_log
        self.last_raw_response: str | None = None

    def apply_batch(
        self,
        batch: MemoryEditBatch,
        *,
        allowed_paths: list[str],
        base_dir: Path,
    ) -> MemoryEditResult:
        """Apply all requests with retries and idempotency checks."""
        applied: list[AppliedItem] = []
        errors: list[ErrorItem] = []
        attempts: dict[str, int] = {}

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
                attempts[req.request_id] = 0
                continue

            request_attempts = 0
            request_errors: list[str] = []
            success = False
            for _ in range(self.max_retries + 1):
                request_attempts += 1

                # Read latest full content before each attempt.
                read_path = (
                    req.index_path
                    if req.kind == "ensure_index_link" and req.index_path
                    else req.target_path
                )
                try:
                    current_file = self._read_current_content(
                        read_path,
                        allowed_paths=allowed_paths,
                        base_dir=base_dir,
                    )
                except ValueError as e:
                    request_errors.append(str(e))
                    break

                decision = self._get_writer_decision(
                    batch=batch,
                    request=req.model_dump(mode="json"),
                    payload_hash=payload_hash,
                    current_file=current_file,
                    previous_errors=request_errors,
                )
                if decision is None:
                    request_errors.append("writer_parse_failed")
                    continue

                if not self._decision_matches_request(decision, req.request_id, req.kind, req.target_path, payload_hash):
                    request_errors.append("writer_decision_mismatch")
                    continue

                if decision.decision == "noop":
                    applied.append(
                        AppliedItem(
                            request_id=req.request_id,
                            status="noop",
                            path=req.target_path,
                        )
                    )
                    self.commit_log.mark_applied(batch.turn_id, req.request_id, payload_hash)
                    success = True
                    break

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
                    success = True
                    break

                request_errors.append(f"{outcome.code}: {outcome.detail}")

            attempts[req.request_id] = request_attempts
            if not success:
                detail = request_errors[-1] if request_errors else "unknown_failure"
                errors.append(
                    ErrorItem(
                        request_id=req.request_id,
                        code="apply_failed",
                        detail=detail,
                    )
                )

        return MemoryEditResult(
            status="ok" if not errors else "failed",
            turn_id=batch.turn_id,
            applied=applied,
            errors=errors,
            writer_attempts=attempts,
        )

    def _read_current_content(
        self,
        path: str,
        *,
        allowed_paths: list[str],
        base_dir: Path,
    ) -> dict[str, object]:
        """Read full file content for writer context."""
        target = resolve_memory_path(path, allowed_paths=allowed_paths, base_dir=base_dir)
        if not target.exists():
            return {
                "exists": False,
                "path": path,
                "content": "",
            }
        if not target.is_file():
            raise ValueError(f"'{path}' is not a file")
        return {
            "exists": True,
            "path": path,
            "content": target.read_text(encoding="utf-8"),
        }

    def _get_writer_decision(
        self,
        *,
        batch: MemoryEditBatch,
        request: dict[str, object],
        payload_hash: str,
        current_file: dict[str, object],
        previous_errors: list[str],
    ) -> WriterDecision | None:
        """Ask writer model for an apply/noop decision and parse JSON output."""
        user_payload = {
            "as_of": batch.as_of,
            "turn_id": batch.turn_id,
            "request": request,
            "payload_hash": payload_hash,
            "current_file": current_file,
            "previous_errors": previous_errors,
        }
        base_messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=json.dumps(user_payload, ensure_ascii=False),
            ),
        ]
        messages = base_messages

        for attempt in range(self.parse_retries + 1):
            raw = self.client.chat(messages)
            self.last_raw_response = raw
            data = extract_json_object(raw)
            if data is not None:
                try:
                    return WriterDecision.model_validate(data)
                except ValueError:
                    pass

            if attempt < self.parse_retries:
                messages = [
                    *base_messages,
                    Message(role="user", content=self.parse_retry_prompt),
                ]
        return None

    @staticmethod
    def _decision_matches_request(
        decision: WriterDecision,
        request_id: str,
        kind: str,
        target_path: str,
        payload_hash: str,
    ) -> bool:
        """Ensure writer response cannot alter semantics."""
        return (
            decision.request_id == request_id
            and decision.kind == kind
            and decision.target_path == target_path
            and decision.payload_hash == payload_hash
        )
