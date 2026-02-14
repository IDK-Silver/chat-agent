"""Shared reviewer helpers and label enforcement for app and shutdown loops."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json

from ..llm.schema import Message, ToolCall
from .schema import (
    AnomalySignal,
    AnomalySignalName,
    RequiredAction,
    TargetSignal,
    TargetSignalName,
)


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
    return paths


def is_memory_edit_index_update(tool_call: ToolCall, index_path: str) -> bool:
    """Check if memory_edit call updates the requested index path."""
    requests = tool_call.arguments.get("requests", [])
    if not isinstance(requests, list):
        return False

    for request in requests:
        if not isinstance(request, dict):
            continue
        req_target = request.get("target_path")
        if req_target == index_path:
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
# Target-signal enforcement and anomaly detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetEnforcementRule:
    """Maps one target signal to required memory paths and repair action payload."""

    signal: TargetSignalName
    action_code: str
    description: str
    target_path: str | None = None
    target_path_glob: str | None = None
    folder_prefix: str | None = None
    index_path: str | None = None

    def matches_path(self, path: str) -> bool:
        """Return True when a write path belongs to this target signal."""
        if self.target_path is not None and path == self.target_path:
            return True
        if self.folder_prefix is not None and path.startswith(self.folder_prefix):
            return True
        if self.target_path_glob is not None and fnmatch(path, self.target_path_glob):
            return True
        return False


_FOLDER_TARGET_RULES: tuple[TargetSignalName, ...] = (
    "target_knowledge",
    "target_experiences",
    "target_thoughts",
    "target_skills",
    "target_interests",
)

_BRAIN_META_TEXT_TOKENS: tuple[str, ...] = (
    "responder",
    "required_actions",
    "tool_calls",
    "retry_instruction",
    "label_signals",
    "target_signals",
    "anomaly_signals",
    "violations",
)


def build_target_enforcement_rules(current_user: str) -> dict[TargetSignalName, TargetEnforcementRule]:
    """Build target-signal rules resolved for current user id."""
    user_profile_path = f"memory/people/user-{current_user}.md"
    return {
        "target_short_term": TargetEnforcementRule(
            signal="target_short_term",
            action_code="persist_target_short_term",
            description="Persist rolling context to memory/agent/short-term.md.",
            target_path="memory/agent/short-term.md",
        ),
        "target_inner_state": TargetEnforcementRule(
            signal="target_inner_state",
            action_code="persist_target_inner_state",
            description="Persist inner-state shift to memory/agent/inner-state.md.",
            target_path="memory/agent/inner-state.md",
        ),
        "target_pending_thoughts": TargetEnforcementRule(
            signal="target_pending_thoughts",
            action_code="persist_target_pending_thoughts",
            description="Persist near-future todos to memory/agent/pending-thoughts.md.",
            target_path="memory/agent/pending-thoughts.md",
        ),
        "target_user_profile": TargetEnforcementRule(
            signal="target_user_profile",
            action_code="persist_target_user_profile",
            description="Persist agent cognition about user to current user profile memory.",
            target_path=user_profile_path,
            folder_prefix="memory/people/",
        ),
        "target_persona": TargetEnforcementRule(
            signal="target_persona",
            action_code="persist_target_persona",
            description="Persist identity/persona updates to memory/agent/persona.md.",
            target_path="memory/agent/persona.md",
        ),
        "target_knowledge": TargetEnforcementRule(
            signal="target_knowledge",
            action_code="persist_target_knowledge",
            description=(
                "Persist durable knowledge to memory/agent/knowledge/*.md "
                "and update memory/agent/knowledge/index.md."
            ),
            target_path_glob="memory/agent/knowledge/*.md",
            folder_prefix="memory/agent/knowledge/",
            index_path="memory/agent/knowledge/index.md",
        ),
        "target_experiences": TargetEnforcementRule(
            signal="target_experiences",
            action_code="persist_target_experiences",
            description=(
                "Persist experiences to memory/agent/experiences/*.md "
                "and update memory/agent/experiences/index.md."
            ),
            target_path_glob="memory/agent/experiences/*.md",
            folder_prefix="memory/agent/experiences/",
            index_path="memory/agent/experiences/index.md",
        ),
        "target_thoughts": TargetEnforcementRule(
            signal="target_thoughts",
            action_code="persist_target_thoughts",
            description=(
                "Persist lessons/thoughts to memory/agent/thoughts/*.md "
                "and update memory/agent/thoughts/index.md."
            ),
            target_path_glob="memory/agent/thoughts/*.md",
            folder_prefix="memory/agent/thoughts/",
            index_path="memory/agent/thoughts/index.md",
        ),
        "target_skills": TargetEnforcementRule(
            signal="target_skills",
            action_code="persist_target_skills",
            description=(
                "Persist skills to memory/agent/skills/*.md "
                "and update memory/agent/skills/index.md."
            ),
            target_path_glob="memory/agent/skills/*.md",
            folder_prefix="memory/agent/skills/",
            index_path="memory/agent/skills/index.md",
        ),
        "target_interests": TargetEnforcementRule(
            signal="target_interests",
            action_code="persist_target_interests",
            description=(
                "Persist interests to memory/agent/interests/*.md "
                "and update memory/agent/interests/index.md."
            ),
            target_path_glob="memory/agent/interests/*.md",
            folder_prefix="memory/agent/interests/",
            index_path="memory/agent/interests/index.md",
        ),
        "target_long_term": TargetEnforcementRule(
            signal="target_long_term",
            action_code="persist_target_long_term",
            description="Persist agreements, long-term TODOs, or critical facts to memory/agent/long-term.md.",
            target_path="memory/agent/long-term.md",
        ),
    }


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


def _collect_memory_write_paths(turn_messages: list[Message]) -> list[str]:
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
            for path in _extract_applied_paths_from_result(msg.content or ""):
                if path.startswith("memory/"):
                    paths.append(path)
    return paths


def has_memory_write_to_any(
    turn_messages: list[Message],
    path_prefixes: tuple[str, ...],
) -> bool:
    """Check if any successful write in turn matches one of the path prefixes."""
    for path in _collect_memory_write_paths(turn_messages):
        if any(path == pfx or path.startswith(pfx) for pfx in path_prefixes):
            return True
    return False


def _has_index_write(paths: list[str], index_path: str) -> bool:
    """Return True when index path was updated by a successful write."""
    return index_path in paths


def _has_folder_content_write(
    paths: list[str],
    *,
    folder_prefix: str,
    index_path: str,
) -> bool:
    """Return True when folder content file (not index) was updated."""
    return any(path.startswith(folder_prefix) and path != index_path for path in paths)


def _is_target_satisfied(rule: TargetEnforcementRule, paths: list[str]) -> bool:
    """Return True when target rule has been fully satisfied in this attempt."""
    if rule.folder_prefix is not None and rule.index_path is not None:
        return _has_folder_content_write(
            paths,
            folder_prefix=rule.folder_prefix,
            index_path=rule.index_path,
        ) and _has_index_write(paths, rule.index_path)
    if rule.target_path is not None:
        return rule.target_path in paths
    if rule.target_path_glob is not None:
        return any(fnmatch(path, rule.target_path_glob) for path in paths)
    return False


def _resolve_target_signal_for_path(
    path: str | None,
    *,
    rules: dict[TargetSignalName, TargetEnforcementRule],
) -> TargetSignalName | None:
    """Resolve target signal by write path."""
    if not isinstance(path, str):
        return None
    for signal, rule in rules.items():
        if rule.matches_path(path):
            return signal
    return None


def _contains_brain_style_meta_text(text: str) -> bool:
    """Detect reviewer meta language in memory-edit instruction text."""
    lowered = text.lower()
    return any(token in lowered for token in _BRAIN_META_TEXT_TOKENS)


def _iter_memory_edit_instructions(turn_messages: list[Message]) -> list[tuple[str | None, str]]:
    """Extract (target_path, instruction) pairs from successful memory_edit calls."""
    pairs: list[tuple[str | None, str]] = []
    for tool_call in collect_turn_tool_calls(turn_messages, include_failed=False):
        if tool_call.name != "memory_edit":
            continue
        requests = tool_call.arguments.get("requests", [])
        if not isinstance(requests, list):
            continue
        for request in requests:
            if not isinstance(request, dict):
                continue
            instruction = request.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                continue
            target_path = request.get("target_path")
            pairs.append((target_path if isinstance(target_path, str) else None, instruction))
    return pairs


def _append_unique_anomaly(
    anomalies: list[AnomalySignal],
    seen: set[tuple[str, str | None, str]],
    *,
    signal: AnomalySignalName,
    target_signal: TargetSignalName | None = None,
    reason: str | None = None,
) -> None:
    """Append anomaly only once across identical signal/target/reason tuples."""
    normalized_reason = (reason or "").strip()
    key = (signal, target_signal, normalized_reason)
    if key in seen:
        return
    seen.add(key)
    anomalies.append(
        AnomalySignal(
            signal=signal,
            target_signal=target_signal,
            reason=reason,
        )
    )


def build_target_enforcement_actions(
    target_signals: list[TargetSignal],
    turn_messages: list[Message],
    *,
    current_user: str,
) -> list[RequiredAction]:
    """Build required actions for required target signals whose writes are unmet."""
    rules = build_target_enforcement_rules(current_user)
    paths = _collect_memory_write_paths(turn_messages)

    actions: list[RequiredAction] = []
    seen_codes: set[str] = set()
    for target_signal in target_signals:
        if not target_signal.requires_persistence:
            continue
        rule = rules.get(target_signal.signal)
        if rule is None:
            continue
        if rule.action_code in seen_codes:
            continue
        if _is_target_satisfied(rule, paths):
            continue
        seen_codes.add(rule.action_code)
        actions.append(
            RequiredAction(
                code=rule.action_code,
                description=rule.description,
                tool="memory_edit",
                target_path=rule.target_path,
                target_path_glob=rule.target_path_glob,
                index_path=rule.index_path,
            )
        )
    return actions


def detect_persistence_anomalies(
    target_signals: list[TargetSignal],
    turn_messages: list[Message],
    *,
    current_user: str,
    attempt_messages: list[Message] | None = None,
) -> list[AnomalySignal]:
    """Deterministically detect target/anomaly mismatches from successful writes.

    Uses dual scope: satisfaction anomalies (missing_required_target,
    missing_index_update) check the full turn so prior-attempt writes count,
    while behavioral anomalies (out_of_contract, wrong_target, brain_style_meta)
    check only the current attempt to avoid re-triggering on stale paths.
    """
    rules = build_target_enforcement_rules(current_user)
    required_rules: dict[TargetSignalName, TargetEnforcementRule] = {}
    for target_signal in target_signals:
        if not target_signal.requires_persistence:
            continue
        rule = rules.get(target_signal.signal)
        if rule is None:
            continue
        required_rules[target_signal.signal] = rule

    turn_paths = _collect_memory_write_paths(turn_messages)
    attempt_paths = (
        _collect_memory_write_paths(attempt_messages)
        if attempt_messages is not None
        else turn_paths
    )
    anomalies: list[AnomalySignal] = []
    seen_anomalies: set[tuple[str, str | None, str]] = set()

    # --- Satisfaction anomalies: use turn_paths (full turn). ---

    # Missing required target writes.
    for target_signal_name, rule in required_rules.items():
        if _is_target_satisfied(rule, turn_paths):
            continue
        _append_unique_anomaly(
            anomalies,
            seen_anomalies,
            signal="anomaly_missing_required_target",
            target_signal=target_signal_name,
            reason=f"Required target not fully satisfied: {target_signal_name}.",
        )

    # Missing index update for folder targets.
    for target_signal_name, rule in required_rules.items():
        if target_signal_name not in _FOLDER_TARGET_RULES:
            continue
        if rule.folder_prefix is None or rule.index_path is None:
            continue
        has_content = _has_folder_content_write(
            turn_paths,
            folder_prefix=rule.folder_prefix,
            index_path=rule.index_path,
        )
        has_index = _has_index_write(turn_paths, rule.index_path)
        if has_content and not has_index:
            _append_unique_anomaly(
                anomalies,
                seen_anomalies,
                signal="anomaly_missing_index_update",
                target_signal=target_signal_name,
                reason=f"Folder target missing index update: {rule.index_path}.",
            )

    # --- Behavioral anomalies: use attempt_paths (current attempt). ---

    # Wrong-target and out-of-contract path writes.
    required_rule_values = tuple(required_rules.values())
    all_rule_values = tuple(rules.values())
    # Only flag wrong-target when required targets are not fully satisfied
    # (checked against turn_paths so prior-attempt writes count).
    all_required_satisfied = all(
        _is_target_satisfied(rule, turn_paths)
        for rule in required_rule_values
    )
    for path in attempt_paths:
        in_contract = any(rule.matches_path(path) for rule in all_rule_values)
        if not in_contract:
            _append_unique_anomaly(
                anomalies,
                seen_anomalies,
                signal="anomaly_out_of_contract_path",
                reason=f"Out-of-contract memory path write: {path}",
            )
            continue
        if required_rule_values and not all_required_satisfied and not any(
            rule.matches_path(path) for rule in required_rule_values
        ):
            _append_unique_anomaly(
                anomalies,
                seen_anomalies,
                signal="anomaly_wrong_target_path",
                reason=f"Memory path write does not match required targets: {path}",
            )

    # Brain style meta text leakage into memory-edit instructions.
    effective_messages = attempt_messages if attempt_messages is not None else turn_messages
    for target_path, instruction in _iter_memory_edit_instructions(effective_messages):
        if not _contains_brain_style_meta_text(instruction):
            continue
        target_signal = _resolve_target_signal_for_path(target_path, rules=rules)
        _append_unique_anomaly(
            anomalies,
            seen_anomalies,
            signal="anomaly_brain_style_meta_text",
            target_signal=target_signal,
            reason="memory_edit instruction contains reviewer meta-language.",
        )

    return anomalies


def merge_anomaly_signals(*groups: list[AnomalySignal]) -> list[AnomalySignal]:
    """Merge anomaly signal groups while deduplicating deterministic duplicates."""
    merged: list[AnomalySignal] = []
    seen: set[tuple[str, str | None, str]] = set()
    for group in groups:
        for anomaly in group:
            _append_unique_anomaly(
                merged,
                seen,
                signal=anomaly.signal,
                target_signal=anomaly.target_signal,
                reason=anomaly.reason,
            )
    return merged
