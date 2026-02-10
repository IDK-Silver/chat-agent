"""Shared reviewer helpers and label enforcement for app and shutdown loops."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json

from ..llm.schema import Message, ToolCall
from .schema import LabelSignal, RequiredAction


def _is_failed_tool_result_message(message: Message) -> bool:
    """Check whether one tool result message indicates failure."""
    if message.role != "tool":
        return False

    content = (message.content or "").strip()
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


def _collect_failed_tool_call_ids(turn_messages: list[Message]) -> set[str]:
    """Collect tool_call ids whose execution result is failed."""
    failed_ids: set[str] = set()
    for message in turn_messages:
        if not _is_failed_tool_result_message(message):
            continue
        if message.tool_call_id:
            failed_ids.add(message.tool_call_id)
    return failed_ids


def collect_turn_tool_calls(
    turn_messages: list[Message],
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
        index_path = request.get("index_path")
        if isinstance(index_path, str) and index_path:
            paths.append(index_path)
    return paths


def is_memory_edit_index_update(tool_call: ToolCall, index_path: str) -> bool:
    """Check if memory_edit call updates the requested index path."""
    requests = tool_call.arguments.get("requests", [])
    if not isinstance(requests, list):
        return False

    for request in requests:
        if not isinstance(request, dict):
            continue
        req_index = request.get("index_path")
        req_target = request.get("target_path")
        if req_index == index_path or req_target == index_path:
            return True
    return False


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


def match_path(path: str, action: RequiredAction) -> bool:
    """Check whether a tool-call path satisfies the action target constraints."""
    # Actions always require concrete target paths; wildcard syntax in the
    # tool-call path means the model did not resolve a real file path.
    if any(ch in path for ch in "*?[]"):
        return False
    if not action.target_path and not action.target_path_glob:
        return True
    if action.target_path and path == action.target_path:
        return True
    if action.target_path_glob and fnmatch(path, action.target_path_glob):
        return True
    return False


def match_action_call(tool_call: ToolCall, action: RequiredAction) -> bool:
    """Check whether one tool call satisfies one required action."""
    if action.tool == "write_or_edit":
        if tool_call.name not in {"write_file", "edit_file", "memory_edit"}:
            return False
    elif action.tool == "memory_edit":
        if tool_call.name != "memory_edit":
            return False
        if not action.target_path and not action.target_path_glob:
            return True
        return any(match_path(path, action) for path in extract_memory_edit_paths(tool_call))
    elif tool_call.name != action.tool:
        return False

    if action.tool in {"write_file", "edit_file", "write_or_edit", "read_file"}:
        if tool_call.name == "memory_edit":
            return any(match_path(path, action) for path in extract_memory_edit_paths(tool_call))
        path = str(tool_call.arguments.get("path", ""))
        return match_path(path, action)

    if action.tool == "execute_shell":
        command = str(tool_call.arguments.get("command", ""))
        if action.command_must_contain and action.command_must_contain not in command:
            return False
        return True

    if action.tool == "get_current_time":
        return True

    return False


def is_action_satisfied(tool_calls: list[ToolCall], action: RequiredAction) -> bool:
    """Verify action completion, including mandatory index update when required."""
    primary_ok = any(match_action_call(tc, action) for tc in tool_calls)
    if not primary_ok:
        return False

    if not action.index_path:
        return True

    return any(
        (
            tc.name in {"write_file", "edit_file"}
            and str(tc.arguments.get("path", "")) == action.index_path
        )
        or (
            tc.name == "memory_edit"
            and is_memory_edit_index_update(tc, action.index_path)
        )
        for tc in tool_calls
    )


def find_missing_actions(
    turn_messages: list[Message],
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Return required actions that were not completed in this attempt."""
    if not required_actions:
        return []

    tool_calls = collect_turn_tool_calls(turn_messages, include_failed=False)
    return [a for a in required_actions if not is_action_satisfied(tool_calls, a)]


# ---------------------------------------------------------------------------
# Label enforcement
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LabelEnforcementRule:
    """Maps a label to required memory paths and the action to inject if unmet."""

    path_prefixes: tuple[str, ...]
    action_code: str
    description: str
    target_path: str | None = None
    target_path_glob: str | None = None


LABEL_ENFORCEMENT_RULES: dict[str, LabelEnforcementRule] = {
    "rolling_context": LabelEnforcementRule(
        path_prefixes=("memory/short-term.md",),
        action_code="persist_rolling_context",
        description="Persist rolling context to memory/short-term.md.",
        target_path="memory/short-term.md",
    ),
    "agent_state_shift": LabelEnforcementRule(
        path_prefixes=("memory/agent/inner-state.md",),
        action_code="persist_agent_state_shift",
        description="Persist agent state shift to memory/agent/inner-state.md.",
        target_path="memory/agent/inner-state.md",
    ),
    "near_future_todo": LabelEnforcementRule(
        path_prefixes=("memory/agent/pending-thoughts.md",),
        action_code="persist_near_future_todo",
        description="Persist near-future todo to memory/agent/pending-thoughts.md.",
        target_path="memory/agent/pending-thoughts.md",
    ),
    "durable_user_fact": LabelEnforcementRule(
        path_prefixes=("memory/agent/knowledge/", "memory/people/"),
        action_code="persist_durable_user_fact",
        description="Persist durable user fact to knowledge or people memory.",
        target_path_glob="memory/agent/knowledge/*.md",
    ),
    "emotional_event": LabelEnforcementRule(
        path_prefixes=("memory/agent/experiences/",),
        action_code="persist_emotional_event",
        description="Persist emotional event to memory/agent/experiences/.",
        target_path_glob="memory/agent/experiences/*.md",
    ),
    "correction_lesson": LabelEnforcementRule(
        path_prefixes=("memory/agent/thoughts/",),
        action_code="persist_correction_lesson",
        description="Persist correction/lesson to memory/agent/thoughts/.",
        target_path_glob="memory/agent/thoughts/*.md",
    ),
    "skill_change": LabelEnforcementRule(
        path_prefixes=("memory/agent/skills/",),
        action_code="persist_skill_change",
        description="Persist skill change to memory/agent/skills/.",
        target_path_glob="memory/agent/skills/*.md",
    ),
    "interest_change": LabelEnforcementRule(
        path_prefixes=("memory/agent/interests/",),
        action_code="persist_interest_change",
        description="Persist interest change to memory/agent/interests/.",
        target_path_glob="memory/agent/interests/*.md",
    ),
    "identity_change": LabelEnforcementRule(
        path_prefixes=("memory/agent/persona.md", "memory/agent/config.md"),
        action_code="sync_identity_persona",
        description="Sync identity changes to memory/agent/persona.md.",
        target_path="memory/agent/persona.md",
    ),
}


def has_memory_write_to_any(
    turn_messages: list[Message],
    path_prefixes: tuple[str, ...],
) -> bool:
    """Check if any tool call in turn wrote to a path matching any prefix."""
    for tool_call in collect_turn_tool_calls(turn_messages, include_failed=False):
        if tool_call.name in {"write_file", "edit_file"}:
            path = str(tool_call.arguments.get("path", ""))
            if any(path == pfx or path.startswith(pfx) for pfx in path_prefixes):
                return True
            continue

        if tool_call.name == "memory_edit":
            for path in extract_memory_edit_paths(tool_call):
                if any(path == pfx or path.startswith(pfx) for pfx in path_prefixes):
                    return True
    return False


def build_label_enforcement_actions(
    label_signals: list[LabelSignal],
    turn_messages: list[Message],
    *,
    threshold: float,
) -> list[RequiredAction]:
    """Build required actions for high-confidence labels whose memory paths are unmet."""
    actions: list[RequiredAction] = []
    seen_codes: set[str] = set()
    for signal in label_signals:
        if signal.confidence < threshold:
            continue
        if not signal.requires_persistence:
            continue
        rule = LABEL_ENFORCEMENT_RULES.get(signal.label)
        if rule is None:
            continue
        if rule.action_code in seen_codes:
            continue
        if has_memory_write_to_any(turn_messages, rule.path_prefixes):
            continue
        seen_codes.add(rule.action_code)
        actions.append(RequiredAction(
            code=rule.action_code,
            description=rule.description,
            tool="memory_edit",
            target_path=rule.target_path,
            target_path_glob=rule.target_path_glob,
        ))
    return actions
