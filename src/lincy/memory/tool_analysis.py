"""Analyze tool call messages for memory-related state.

Extracted from reviewer/enforcement.py — only the functions needed by
memory sync side-channel and responder tool loop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm.content import content_to_text
from ..llm.schema import ToolCall
from ..session.schema import SessionEntry


MEMORY_SYNC_TARGETS: tuple[str, ...] = (
    "memory/agent/temp-memory.md",
)
ARTIFACT_REGISTRY_TARGET = "memory/agent/artifacts.md"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_missing_memory_sync_targets(
    turn_messages: list[SessionEntry],
    targets: tuple[str, ...] = MEMORY_SYNC_TARGETS,
) -> list[str]:
    """Return memory sync target paths not written in this turn."""
    written = set(_collect_memory_write_paths(turn_messages))
    return [t for t in targets if t not in written]


def find_missing_artifact_registry_paths(
    turn_messages: list[SessionEntry],
    *,
    agent_os_dir: Path,
    registry_target: str = ARTIFACT_REGISTRY_TARGET,
) -> list[str]:
    """Return artifact paths written this turn when registry sync is missing."""
    artifact_paths = _collect_artifact_write_paths(
        turn_messages,
        agent_os_dir=agent_os_dir,
    )
    if not artifact_paths:
        return []

    written_memory = set(_collect_memory_write_paths(turn_messages))
    if registry_target in written_memory:
        return []
    return artifact_paths


def extract_memory_edit_paths(tool_call: ToolCall) -> list[str]:
    """Extract all relevant memory paths from a memory_edit tool call."""
    requests = tool_call.arguments.get("requests", [])
    if not isinstance(requests, list):
        return []

    paths: list[str] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        target_path = request.get("target_path")
        if isinstance(target_path, str) and target_path:
            paths.append(target_path)
    return paths


def is_failed_memory_edit_result(result: str) -> bool:
    """Check whether a memory_edit tool result indicates failure."""
    if result.startswith("Error"):
        return True
    if not result.startswith("{"):
        return False
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "failed"


def summarize_memory_edit_failure(result: str) -> str | None:
    """Summarize a failed memory_edit result for UI warnings."""
    text = (result or "").strip()
    if not text:
        return None
    if text.startswith("Error"):
        return "tool_error"
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "failed":
        return None

    applied = payload.get("applied")
    errors = payload.get("errors")
    applied_count = len(applied) if isinstance(applied, list) else 0
    error_items = errors if isinstance(errors, list) else []
    error_count = len(error_items)

    codes: list[str] = []
    status_hints: list[str] = []
    for item in error_items:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
        detail = item.get("detail")
        if isinstance(detail, str):
            match = re.search(r"\b(5\d{2}|429)\b", detail)
            if match:
                hint = match.group(1)
                if hint not in status_hints:
                    status_hints.append(hint)

    parts: list[str] = []
    if codes:
        code_text = ",".join(codes[:2])
        if len(codes) > 2:
            code_text += ",+"
        if status_hints:
            code_text += f" ({','.join(status_hints[:2])})"
        parts.append(code_text)
    if error_count:
        parts.append(f"errors={error_count}")
    if applied_count:
        parts.append(f"applied={applied_count}")
    return "; ".join(parts) or "failed"


def collect_turn_tool_calls(
    turn_messages: list[SessionEntry],
    *,
    include_failed: bool = True,
) -> list[ToolCall]:
    """Collect tool calls made in a single responder attempt."""
    tool_calls: list[ToolCall] = []
    for msg in turn_messages:
        if msg.role == "assistant" and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    if include_failed:
        return tool_calls
    failed_ids = _collect_failed_tool_call_ids(turn_messages)
    if not failed_ids:
        return tool_calls
    return [tc for tc in tool_calls if tc.id not in failed_ids]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_memory_write_paths(turn_messages: list[SessionEntry]) -> list[str]:
    """Collect successful memory write paths from this attempt."""
    paths: list[str] = []
    # write_file / edit_file: arguments-based, exclude failed calls.
    for tool_call in collect_turn_tool_calls(turn_messages, include_failed=False):
        if tool_call.name in {"write_file", "edit_file"}:
            path = str(tool_call.arguments.get("path", ""))
            if path.startswith("memory/"):
                paths.append(path)
    # memory_edit: extract from result message (handles partial failures).
    for msg in turn_messages:
        if msg.role == "tool" and msg.name == "memory_edit":
            for path in _extract_applied_paths_from_result(content_to_text(msg.content)):
                if path.startswith("memory/"):
                    paths.append(path)
    return paths


def _collect_artifact_write_paths(
    turn_messages: list[SessionEntry],
    *,
    agent_os_dir: Path,
) -> list[str]:
    """Collect successful artifact write paths from this attempt."""
    paths: list[str] = []
    for tool_call in collect_turn_tool_calls(turn_messages, include_failed=False):
        if tool_call.name not in {"write_file", "edit_file"}:
            continue
        path = tool_call.arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        artifact_path = _normalize_artifact_path(path, agent_os_dir=agent_os_dir)
        if artifact_path is None:
            continue
        paths.append(artifact_path)

    return list(dict.fromkeys(paths))


def _normalize_artifact_path(raw_path: str, *, agent_os_dir: Path) -> str | None:
    """Normalize artifact path to a relative runtime path."""
    normalized = raw_path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]

    if normalized.startswith("artifacts/"):
        return normalized

    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = agent_os_dir / candidate

    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(agent_os_dir.resolve())
    except ValueError:
        return None

    relative_text = relative.as_posix()
    if not relative_text.startswith("artifacts/"):
        return None
    return relative_text


def _extract_applied_paths_from_result(content: str) -> list[str]:
    """Parse memory_edit result JSON and return paths from applied items."""
    if not content or not content.strip().startswith("{"):
        return []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    applied = payload.get("applied")
    if not isinstance(applied, list):
        return []
    paths: list[str] = []
    for item in applied:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def _is_failed_tool_result_message(message: SessionEntry) -> bool:
    """Check whether one tool result message indicates failure."""
    if message.role != "tool":
        return False

    content = content_to_text(message.content).strip()
    if not content:
        return False

    if message.name == "memory_edit":
        return is_failed_memory_edit_result(content)

    if content.startswith("Error"):
        return True
    if not content.startswith("{"):
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "failed"


def _collect_failed_tool_call_ids(turn_messages: list[SessionEntry]) -> set[str]:
    """Collect tool_call ids whose execution result is failed."""
    failed_ids: set[str] = set()
    for message in turn_messages:
        if not _is_failed_tool_result_message(message):
            continue
        if message.tool_call_id:
            failed_ids.add(message.tool_call_id)
    return failed_ids
