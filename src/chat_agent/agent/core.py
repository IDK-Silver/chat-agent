"""Agent core logic: responder + memory sync + post-review + shutdown.

Extracted from cli/app.py to decouple agent logic from CLI adapter.
"""

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import uuid

from ..cli.console import ChatConsole
from ..cli.interrupt import EscInterruptMonitor
from ..context import ContextBuilder, Conversation
from ..core.schema import AppConfig, ToolsConfig
from ..llm import LLMResponse
from ..llm.base import LLMClient
from ..llm.schema import ContextLengthExceededError, Message, ToolCall, ToolDefinition
from ..memory import (
    MemoryEditor,
    MEMORY_EDIT_DEFINITION,
    MEMORY_SEARCH_DEFINITION,
    MemorySearchAgent,
    create_memory_edit,
    create_memory_search,
)
from ..memory.backup import MemoryBackupManager
from ..memory.hooks import check_and_archive_buffers
from ..reviewer import (
    PostReviewer,
    ProgressReviewer,
    RequiredAction,
    ReviewPacketConfig,
    build_post_review_packet,
)
from ..reviewer.enforcement import (
    collect_turn_tool_calls,
    extract_memory_edit_paths,
    find_missing_memory_sync_targets,
    is_failed_memory_edit_result,
    find_missing_actions,
)
from ..reviewer.json_extract import extract_json_object
from ..reviewer.schema import AnomalySignal, PostReviewResult, TargetSignal
from ..session import SessionManager
from ..tools import (
    ToolRegistry,
    ShellExecutor,
    get_current_time,
    GET_CURRENT_TIME_DEFINITION,
    EXECUTE_SHELL_DEFINITION,
    create_execute_shell,
    READ_FILE_DEFINITION,
    WRITE_FILE_DEFINITION,
    EDIT_FILE_DEFINITION,
    create_read_file,
    create_write_file,
    create_edit_file,
    READ_IMAGE_DEFINITION,
    READ_IMAGE_BY_SUBAGENT_DEFINITION,
    create_read_image_vision,
    create_read_image_with_sub_agent,
    create_read_image_by_subagent,
    VisionAgent,
)
from ..gui import (
    GUI_TASK_DEFINITION,
    GUIManager,
    SCREENSHOT_DEFINITION,
    create_gui_task,
    create_screenshot,
)
from ..workspace import WorkspaceManager
from .shutdown import perform_shutdown, _has_conversation_content

_MEMORY_EDIT_RETRY_LIMIT = 3
_DEBUG_RESPONSE_PREVIEW_CHARS = 4000
_SENSITIVE_URL_PARAM_RE = re.compile(r"([?&](?:key|api_key|token|access_token)=)[^&\s]+", re.IGNORECASE)
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
logger = logging.getLogger(__name__)


def _latest_nonempty_assistant_content(messages: list[Message]) -> str:
    """Return the newest non-empty assistant content from non-tool messages."""
    for msg in reversed(messages):
        if msg.role != "assistant" or msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or ""
    return ""


def _latest_intermediate_text(messages: list[Message]) -> str:
    """Return newest non-empty content from assistant messages that have tool_calls."""
    for msg in reversed(messages):
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or ""
    return ""


def _resolve_final_content(
    response_content: str | None,
    turn_messages: list[Message],
) -> tuple[str, bool]:
    """Resolve user-visible content; fallback to prior assistant tool-call text."""
    if isinstance(response_content, str) and response_content.strip():
        return response_content, False

    fallback = _latest_nonempty_assistant_content(turn_messages)
    if fallback:
        return fallback, True

    return "", False


def _turn_has_visible_intermediate_text(turn_messages: list[Message]) -> bool:
    """Return True when this turn already displayed non-empty intermediate text."""
    for msg in turn_messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return True
    return False


def _debug_print_responder_output(
    console: ChatConsole,
    response: LLMResponse,
    *,
    label: str,
) -> None:
    """Print responder model output details for debug investigations."""
    if not console.debug:
        return

    tool_calls = response.tool_calls or []
    tool_names = ", ".join(tc.name for tc in tool_calls) if tool_calls else "(none)"
    content = response.content or ""
    console.print_debug(
        label,
        f"content_chars={len(content)}, tool_calls={len(tool_calls)}, tools=[{tool_names}]",
    )

    if not content.strip():
        if tool_calls:
            console.print_debug(
                f"{label} output",
                "(tool-only response; no textual content)",
            )
        else:
            console.print_debug(
                f"{label} output",
                "(empty; no textual content and no tool calls)",
            )
        return

    preview = content
    if len(preview) > _DEBUG_RESPONSE_PREVIEW_CHARS:
        preview = (
            preview[:_DEBUG_RESPONSE_PREVIEW_CHARS]
            + "\n...[truncated]"
        )
    console.print_debug_block(f"{label} output", preview)


def _normalize_memory_path(path: str) -> str:
    """Normalize path string for memory path checks."""
    return path.strip().replace("\\", "/")


def _is_memory_path(path: str, *, agent_os_dir: Path) -> bool:
    """Check whether a path points to memory/ in relative or absolute form."""
    normalized = _normalize_memory_path(path)
    if normalized.startswith("./"):
        normalized = normalized[2:]

    if normalized == "memory" or normalized.startswith("memory/"):
        return True
    if normalized.startswith(".agent/memory/"):
        return True

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = agent_os_dir / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to((agent_os_dir / "memory").resolve())
        return True
    except Exception:
        return False


@dataclass
class _MemoryFileSnapshot:
    """Original state of one memory file before the current turn writes it."""

    existed: bool
    was_file: bool
    content: bytes | None = None


class _TurnMemorySnapshot:
    """Capture/rollback memory file changes made during one user turn."""

    def __init__(self, *, agent_os_dir: Path):
        self._agent_os_dir = agent_os_dir
        self._memory_root = (agent_os_dir / "memory").resolve()
        self._snapshots: dict[Path, _MemoryFileSnapshot] = {}

    def capture_from_tool_call(self, tool_call: ToolCall) -> None:
        """Snapshot all memory paths referenced by a memory_edit call."""
        if tool_call.name != "memory_edit":
            return

        for path in extract_memory_edit_paths(tool_call):
            resolved = self._resolve_memory_file(path)
            if resolved is None or resolved in self._snapshots:
                continue

            if resolved.exists():
                if resolved.is_file():
                    self._snapshots[resolved] = _MemoryFileSnapshot(
                        existed=True,
                        was_file=True,
                        content=resolved.read_bytes(),
                    )
                else:
                    self._snapshots[resolved] = _MemoryFileSnapshot(
                        existed=True,
                        was_file=False,
                    )
            else:
                self._snapshots[resolved] = _MemoryFileSnapshot(
                    existed=False,
                    was_file=False,
                )

    def rollback(self) -> int:
        """Restore all captured files to their pre-turn state."""
        restored = 0

        # Restore deep paths first so recreations/deletions do not conflict.
        for path in sorted(self._snapshots.keys(), key=lambda p: len(p.parts), reverse=True):
            snapshot = self._snapshots[path]
            if snapshot.existed:
                if not snapshot.was_file:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot.content or b"")
                restored += 1
                continue

            if path.exists() and path.is_file():
                path.unlink()
                restored += 1

        return restored

    def _resolve_memory_file(self, raw_path: str) -> Path | None:
        normalized = _normalize_memory_path(raw_path)
        if normalized.startswith("./"):
            normalized = normalized[2:]

        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = self._agent_os_dir / candidate

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._memory_root)
        except ValueError:
            return None
        return resolved


def _build_memory_shell_write_patterns(agent_os_dir: Path) -> list[re.Pattern[str]]:
    """Build shell patterns that indicate direct memory writes."""
    memory_abs = re.escape(str((agent_os_dir / "memory").resolve()))
    memory_rel = r"(?:\./)?(?:\.agent/)?memory/"
    memory_target = rf"(?:['\"])?(?:{memory_rel}|{memory_abs}/)"
    return [
        re.compile(rf">>?\s*{memory_target}"),
        re.compile(rf"\btee(?:\s+-a)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\bsed\s+-i(?:\S*)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\brm\s[^\n]*{memory_target}"),
        re.compile(rf"\bmv\s[^\n]*{memory_target}"),
    ]


def _is_memory_write_shell_command(command: str, *, agent_os_dir: Path) -> bool:
    """Check if command contains shell patterns that write under memory/."""
    return any(
        pattern.search(command) is not None
        for pattern in _build_memory_shell_write_patterns(agent_os_dir)
    )


def _has_memory_write(turn_messages: list[Message]) -> bool:
    """Check whether this responder attempt wrote any memory file."""
    for tool_call in collect_turn_tool_calls(turn_messages, include_failed=False):
        if tool_call.name in {"write_file", "edit_file"}:
            path = str(tool_call.arguments.get("path", ""))
            if path.startswith("memory/"):
                return True
            continue

        if tool_call.name == "memory_edit":
            for path in extract_memory_edit_paths(tool_call):
                if path.startswith("memory/"):
                    return True
    return False


def _build_post_review_packet_messages(
    conversation_messages: list[Message],
    *,
    turn_anchor: int,
    attempt_anchor: int,
) -> list[Message]:
    """Scope post-review packet to current retry attempt while keeping user turn."""
    if attempt_anchor <= turn_anchor:
        return list(conversation_messages)

    if turn_anchor < 0 or turn_anchor >= len(conversation_messages):
        return list(conversation_messages)

    turn_head = conversation_messages[turn_anchor:attempt_anchor]
    latest_user_turn = next((msg for msg in turn_head if msg.role == "user"), None)
    if latest_user_turn is None:
        return list(conversation_messages)

    return [
        *conversation_messages[:turn_anchor],
        latest_user_turn,
        *conversation_messages[attempt_anchor:],
    ]


def _filter_retry_violations(
    violations: list[str],
    *,
    turn_messages: list[Message],
) -> list[str]:
    """Filter stale violations that conflict with deterministic tool evidence."""
    if not violations:
        return []

    if not _has_memory_write(turn_messages):
        return list(violations)

    return [v for v in violations if v != "turn_not_persisted"]


def _collect_required_actions_for_retry(
    turn_messages: list[Message],
    *,
    passed: bool,
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Select required actions that still need retry for this attempt."""
    if passed:
        return []
    missing_actions = find_missing_actions(turn_messages, required_actions)
    if required_actions and missing_actions:
        return missing_actions
    if required_actions and not missing_actions:
        return []
    return required_actions


def _build_turn_persistence_action() -> RequiredAction:
    """Build fallback action to force minimum per-turn memory persistence."""
    return RequiredAction(
        code="persist_turn_memory",
        description=(
            "Persist this turn to rolling memory via memory/agent/short-term.md "
            "before finalizing the user-facing answer."
        ),
        tool="memory_edit",
        target_path="memory/agent/short-term.md",
    )


def _ensure_turn_persistence_action(
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Append per-turn persistence action if not already covered."""
    for action in required_actions:
        if action.code == "persist_turn_memory":
            return required_actions
        if action.tool in {"write_file", "edit_file", "write_or_edit", "memory_edit"}:
            if action.target_path and action.target_path.startswith("memory/"):
                return required_actions
            if action.target_path_glob and action.target_path_glob.startswith("memory/"):
                return required_actions

    return [*required_actions, _build_turn_persistence_action()]


def _build_memory_edit_retry_hints(action: RequiredAction) -> list[str]:
    """Build precise memory_edit hints for retry directives."""
    hints = [
        "   - use exact keys: as_of, turn_id, requests",
    ]

    if action.target_path:
        hints.append(
            "   - memory_edit minimal payload: "
            '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
            '"requests":[{"request_id":"r1",'
            f'"target_path":"{action.target_path}",'
            '"instruction":"<what to change>"}]}'
        )
        return hints

    if action.target_path_glob:
        glob_target = action.target_path_glob
        base_dir = ""
        if "/" in glob_target:
            base_dir = glob_target.rsplit("/", 1)[0] + "/"

        hints.extend([
            "   - target_path_glob is a directory constraint, not a writable target_path.",
            "   - NEVER use wildcard characters in requests[].target_path.",
            "   - First call memory_search to locate an existing concrete file path.",
            "   - existing-file payload: "
            '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
            '"requests":[{"request_id":"r1",'
            '"target_path":"<exact_path_not_glob>",'
            '"instruction":"<what to change>"}]}',
        ])
        if base_dir:
            hints.append(
                "   - if no file exists, create one under "
                f"{base_dir}<new-file>.md"
            )
        else:
            hints.append(
                "   - if no file exists, create one using a concrete target_path."
            )
        if action.index_path:
            hints.append(
                "   - if index update is required, write target_path to "
                f"{action.index_path}"
            )
        return hints

    hints.append(
        "   - memory_edit minimal payload: "
        '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
        '"requests":[{"request_id":"r1",'
        '"target_path":"memory/agent/short-term.md",'
        '"instruction":"<what to change>"}]}'
    )
    return hints


def _build_memory_sync_reminder(missing_targets: list[str]) -> str:
    """Build directive for the memory-sync side-channel LLM call."""
    targets = "\n".join(f"- {t}" for t in missing_targets)
    return (
        "[MEMORY SYNC]\n"
        f"You have not updated the following files this turn:\n{targets}\n"
        "Call memory_edit to update them now."
    )


def _build_retry_directive(
    required_actions: list[RequiredAction],
    retry_instruction: str = "",
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Build directive for post-review retry.

    Injected as a synthetic tool result so the LLM treats it as
    authoritative without mutating system_instruction.
    """
    parts: list[str] = [
        "[RETRY CONTRACT]",
        "(_post_review is an automated internal tool. Never call it yourself.)",
    ]
    if attempt is not None and max_attempts is not None:
        parts.append(f"attempt: {attempt}/{max_attempts}")
    parts.append("state: FAILED_PREVIOUS_ATTEMPT")

    missing_targets: list[str] = []
    seen_targets: set[str] = set()
    for action in required_actions:
        candidates: list[str] = []
        if action.target_path:
            candidates.append(action.target_path)
        elif action.target_path_glob:
            candidates.append(f"<one concrete path matching {action.target_path_glob}>")
        if action.index_path:
            candidates.append(action.index_path)
        for candidate in candidates:
            if candidate in seen_targets:
                continue
            seen_targets.add(candidate)
            missing_targets.append(candidate)

    if required_actions:
        parts.append("Complete ALL required actions below before responding to the user.")
        parts.extend(["", "missing_targets:"])
        for path in missing_targets:
            parts.append(f"- {path}")
        parts.append("")
        parts.append("Required actions:")
        for i, action in enumerate(required_actions, start=1):
            parts.append(f"{i}. [{action.code}] {action.description}")
            parts.append(f"   - tool: {action.tool}")
            if action.target_path:
                parts.append(f"   - target_path: {action.target_path}")
            if action.target_path_glob:
                parts.append(f"   - target_path_glob: {action.target_path_glob}")
            if action.index_path:
                parts.append(f"   - index_path: {action.index_path}")
            if action.tool == "memory_edit":
                parts.extend(_build_memory_edit_retry_hints(action))

    if retry_instruction:
        parts.append("")
        parts.append(retry_instruction)

    if required_actions:
        parts.extend([
            "",
            "completion_criteria:",
            "- Every required action above is completed successfully in this attempt.",
            "- All missing_targets listed above are written successfully.",
            "",
            "hard_rule:",
            "- Do NOT output user-facing reply before completion.",
            "- If completion_criteria is not met, continue tool calls now.",
        ])
        parts.append("")
        parts.append("Execute now.")
    else:
        parts.extend([
            "",
            "No additional tool actions are required.",
            "Provide the final user-facing reply now.",
        ])

    return "\n".join(parts)


def _build_missing_visible_reply_directive(
    retry_instruction: str,
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Build retry directive when turn has no visible assistant text yet."""
    parts: list[str] = [
        "[RETRY CONTRACT]",
        "(_post_review is an automated internal tool. Never call it yourself.)",
    ]
    if attempt is not None and max_attempts is not None:
        parts.append(f"attempt: {attempt}/{max_attempts}")
    parts.extend([
        "state: FAILED_PREVIOUS_ATTEMPT",
        "A user-visible final reply is required for this turn.",
        "",
        "Requirements:",
        "- Output one assistant reply to the user in this attempt.",
        "- The reply must not be empty or whitespace.",
    ])
    if retry_instruction:
        parts.extend(["", retry_instruction])
    parts.extend(["", "Execute now."])
    return "\n".join(parts)


def _resolve_effective_target_signals(
    current_target_signals: list[TargetSignal],
    sticky_target_signals: dict[str, TargetSignal],
) -> list[TargetSignal]:
    """Merge current target signals with sticky unresolved targets in this turn."""
    for signal in current_target_signals:
        if signal.requires_persistence:
            sticky_target_signals[signal.signal] = signal

    effective = list(sticky_target_signals.values())
    for signal in current_target_signals:
        if signal.signal not in sticky_target_signals:
            effective.append(signal)
    return effective


def _promote_anomaly_targets_to_sticky(
    sticky_target_signals: dict[str, TargetSignal],
    anomaly_signals: list[AnomalySignal],
) -> None:
    """Promote anomaly target references to sticky required targets for retries."""
    for anomaly in anomaly_signals:
        target_signal = anomaly.target_signal
        if not target_signal or target_signal in sticky_target_signals:
            continue
        sticky_target_signals[target_signal] = TargetSignal(
            signal=target_signal,
            requires_persistence=True,
            reason=anomaly.reason or "Carry-over target from unresolved anomaly.",
        )


def _action_signature(
    required_actions: list[RequiredAction],
    violations: list[str],
    anomaly_signals: list[AnomalySignal] | None = None,
) -> tuple[str, ...]:
    """Build stable signature for retry loop guard."""
    parts: list[str] = []
    parts.extend(f"action:{code}" for code in sorted(a.code for a in required_actions))
    parts.extend(f"violation:{v.lower()}" for v in sorted(violations))
    if anomaly_signals:
        parts.extend(
            "anomaly:" + sig.signal + ":" + (sig.target_signal or "-")
            for sig in sorted(
                anomaly_signals,
                key=lambda s: (s.signal, s.target_signal or "", s.reason or ""),
            )
        )
    return tuple(parts)


def _format_anomaly_retry_instruction(anomaly_signals: list[AnomalySignal]) -> str:
    """Build retry instruction text from anomaly signals."""
    if not anomaly_signals:
        return ""
    lines = ["Fix all anomaly signals before final answer:"]
    for idx, anomaly in enumerate(anomaly_signals, start=1):
        target = anomaly.target_signal or "-"
        reason = anomaly.reason or "no reason provided"
        lines.append(f"{idx}. {anomaly.signal} | target={target} | {reason}")
    return "\n".join(lines)


def _format_debug_json(raw: str) -> str:
    """Try to pretty-print JSON from raw LLM output for debug display."""
    data = extract_json_object(raw)
    if data is not None:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return raw


def _build_reviewer_warning(
    stage: str,
    raw_response: str | None,
    error_detail: str | None = None,
) -> str:
    """Build human-readable warning when a reviewer pass fails."""
    if raw_response is None:
        warning = (
            f"{stage} failed due to model call error; skipping this pass for current turn."
        )
        if error_detail:
            warning += f" reason: {_sanitize_error_message(error_detail)}"
        return warning
    return (
        f"{stage} returned invalid JSON/schema; skipping this pass for current turn."
    )


def _sanitize_error_message(message: str) -> str:
    """Redact known sensitive tokens from surfaced error messages."""
    redacted = _SENSITIVE_URL_PARAM_RE.sub(r"\1***", message)
    return _GOOGLE_API_KEY_RE.sub("***", redacted)


def _rollback_turn_memory_changes(
    snapshot: _TurnMemorySnapshot,
    *,
    console: ChatConsole,
    debug: bool,
) -> None:
    """Best-effort rollback for partial turn memory writes."""
    try:
        restored = snapshot.rollback()
    except Exception:
        logger.exception("Failed to rollback memory writes for failed turn")
        console.print_warning("Failed to rollback partial memory writes for failed turn.")
        return

    if debug and restored > 0:
        console.print_debug("turn rollback", f"restored {restored} memory file(s)")


def setup_tools(
    tools_config: ToolsConfig,
    agent_os_dir: Path,
    *,
    memory_editor: MemoryEditor | None = None,
    memory_search_agent: MemorySearchAgent | None = None,
    brain_has_vision: bool = False,
    use_own_vision_ability: bool = False,
    vision_agent: VisionAgent | None = None,
    gui_manager: GUIManager | None = None,
    screenshot_max_width: int | None = None,
    screenshot_quality: int = 80,
) -> ToolRegistry:
    """Set up the tool registry with built-in tools.

    Args:
        tools_config: Tools configuration
        agent_os_dir: Application working directory (for file access)
    """
    registry = ToolRegistry()

    # Time tool
    registry.register("get_current_time", get_current_time, GET_CURRENT_TIME_DEFINITION)

    # Shell executor - use agent_os_dir
    executor = ShellExecutor(
        agent_os_dir=agent_os_dir,
        blacklist=tools_config.shell.blacklist,
        timeout=tools_config.shell.timeout,
    )
    base_execute_shell = create_execute_shell(executor)

    def guarded_execute_shell(command: str, timeout: int | None = None) -> str:
        if _is_memory_write_shell_command(command, agent_os_dir=agent_os_dir):
            return "Error: Direct memory writes via shell are blocked. Use memory_edit."
        return base_execute_shell(command, timeout)

    registry.register("execute_shell", guarded_execute_shell, EXECUTE_SHELL_DEFINITION)

    # File tools - allow access to agent_os_dir
    allowed_paths = list(tools_config.allowed_paths)
    # Always allow agent_os_dir for memory access
    allowed_paths.insert(0, str(agent_os_dir))
    # Allow reading GUI capture screenshots from temp dir
    if gui_manager is not None:
        allowed_paths.append(gui_manager.capture_dir)

    registry.register(
        "read_file",
        create_read_file(allowed_paths, agent_os_dir),
        READ_FILE_DEFINITION,
    )
    base_write_file = create_write_file(allowed_paths, agent_os_dir)
    base_edit_file = create_edit_file(allowed_paths, agent_os_dir)

    def guarded_write_file(path: str, content: str) -> str:
        if _is_memory_path(path, agent_os_dir=agent_os_dir):
            return "Error: Direct memory writes are blocked. Use memory_edit."
        return base_write_file(path, content)

    def guarded_edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        if _is_memory_path(path, agent_os_dir=agent_os_dir):
            return "Error: Direct memory edits are blocked. Use memory_edit."
        return base_edit_file(path, old_string, new_string, replace_all)

    registry.register("write_file", guarded_write_file, WRITE_FILE_DEFINITION)
    registry.register("edit_file", guarded_edit_file, EDIT_FILE_DEFINITION)

    if memory_editor is not None:
        registry.register(
            "memory_edit",
            create_memory_edit(
                memory_editor,
                allowed_paths=allowed_paths,
                base_dir=agent_os_dir,
            ),
            MEMORY_EDIT_DEFINITION,
        )

    if memory_search_agent is not None:
        registry.register(
            "memory_search",
            create_memory_search(
                memory_search_agent,
                allow_failure=tools_config.memory_search.allow_failure,
            ),
            MEMORY_SEARCH_DEFINITION,
        )

    # Image tool -- uses the same allowed_paths as other file tools.
    if brain_has_vision and not use_own_vision_ability and vision_agent is not None:
        # Brain has vision but delegates to sub-agent (avoids large payloads)
        registry.register(
            "read_image_by_subagent",
            create_read_image_by_subagent(allowed_paths, agent_os_dir, vision_agent),
            READ_IMAGE_BY_SUBAGENT_DEFINITION,
        )
    elif brain_has_vision:
        registry.register(
            "read_image",
            create_read_image_vision(allowed_paths, agent_os_dir),
            READ_IMAGE_DEFINITION,
        )
    elif vision_agent is not None:
        registry.register(
            "read_image",
            create_read_image_with_sub_agent(allowed_paths, agent_os_dir, vision_agent),
            READ_IMAGE_DEFINITION,
        )

    # Screenshot tool -- direct screenshot for brain's vision model
    if brain_has_vision:
        registry.register(
            "screenshot",
            create_screenshot(
                max_width=screenshot_max_width,
                quality=screenshot_quality,
            ),
            SCREENSHOT_DEFINITION,
        )

    # GUI automation tool
    if gui_manager is not None:
        registry.register(
            "gui_task",
            create_gui_task(gui_manager),
            GUI_TASK_DEFINITION,
        )

    return registry


def _patch_interrupted_tool_calls(conversation: Conversation, since: int) -> int:
    """Fill missing tool results for interrupted tool calls. Returns count added."""
    messages = conversation.get_messages()
    # Find last assistant message with tool_calls after `since`
    last_assistant_idx = None
    for i in range(len(messages) - 1, since - 1, -1):
        if messages[i].role == "assistant" and messages[i].tool_calls:
            last_assistant_idx = i
            break
    if last_assistant_idx is None:
        return 0

    existing = {
        messages[i].tool_call_id
        for i in range(last_assistant_idx + 1, len(messages))
        if messages[i].role == "tool" and messages[i].tool_call_id
    }
    added = 0
    for tc in messages[last_assistant_idx].tool_calls:
        if tc.id not in existing:
            conversation.add_tool_result(tc.id, tc.name, "[Interrupted by user]")
            added += 1
    return added


def _run_responder(
    client: LLMClient,
    messages: list[Message],
    tools: list[ToolDefinition],
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    memory_edit_allow_failure: bool = False,
    progress_reviewer: ProgressReviewer | None = None,
    progress_review_warn_on_failure: bool = True,
) -> LLMResponse:
    """Run responder with tool call loop. Returns final response."""
    with console.spinner():
        response = client.chat_with_tools(messages, tools)
    _debug_print_responder_output(console, response, label="responder")

    memory_edit_fail_streak = 0
    while response.has_tool_calls():
        chunk = response.content or ""
        if chunk.strip():
            console.print_assistant(chunk)
            if progress_reviewer is not None:
                review_messages = builder.build(conversation)
                with console.spinner("Checking..."):
                    progress_result = progress_reviewer.review(
                        review_messages,
                        candidate_reply=chunk,
                    )
                if progress_result is None:
                    if progress_review_warn_on_failure:
                        console.print_warning(
                            _build_reviewer_warning(
                                "Progress-review",
                                progress_reviewer.last_raw_response,
                                progress_reviewer.last_error,
                            )
                        )
                    if console.debug:
                        raw = progress_reviewer.last_raw_response or "(empty)"
                        console.print_debug_block(
                            "progress-review raw",
                            _format_debug_json(raw),
                        )
                        if progress_reviewer.last_error:
                            console.print_debug(
                                "progress-review error",
                                _sanitize_error_message(progress_reviewer.last_error),
                            )
                elif not progress_result.passed:
                    if console.debug:
                        for violation in progress_result.violations:
                            console.print_debug("progress-review violation", violation)
                        if progress_result.block_instruction:
                            console.print_debug(
                                "progress-review instruction",
                                progress_result.block_instruction,
                            )
                    elif progress_review_warn_on_failure:
                        console.print_warning(
                            "Progress-review flagged one intermediate text chunk.",
                        )

        conversation.add_assistant_with_tools(response.content, response.tool_calls)

        failed_memory_edit_this_round = False
        for tool_call in response.tool_calls:
            if not registry.has_tool(tool_call.name):
                conversation.add_tool_result(
                    tool_call.id, tool_call.name,
                    f"Error: Unknown tool '{tool_call.name}'",
                )
                continue
            console.print_tool_call(tool_call)
            if on_before_tool_call is not None:
                on_before_tool_call(tool_call)
            # gui_task has its own step-by-step output; spinner would conflict
            if tool_call.name == "gui_task":
                result = registry.execute(tool_call)
            else:
                with console.spinner("Executing..."):
                    result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)
            if tool_call.name == "memory_edit" and isinstance(result, str) and is_failed_memory_edit_result(result):
                failed_memory_edit_this_round = True

        if failed_memory_edit_this_round:
            memory_edit_fail_streak += 1
            if memory_edit_fail_streak >= _MEMORY_EDIT_RETRY_LIMIT:
                if memory_edit_allow_failure:
                    console.print_warning(
                        f"memory_edit failed {memory_edit_fail_streak} times; "
                        "allow_failure=true, continuing turn.",
                    )
                    break
                raise RuntimeError(
                    f"memory_edit failed {memory_edit_fail_streak} times; fail-closed for this turn."
                )
            console.print_warning(
                f"memory_edit failed; retrying ({memory_edit_fail_streak}/{_MEMORY_EDIT_RETRY_LIMIT})",
                indent=2,
            )
        else:
            memory_edit_fail_streak = 0

        messages = builder.build(conversation)
        with console.spinner():
            response = client.chat_with_tools(messages, tools)
        _debug_print_responder_output(console, response, label="responder")

    return response


def _run_memory_sync_side_channel(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
    missing_targets: list[str],
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
) -> None:
    """One-shot side-channel LLM call to sync missing memory targets.

    Builds a local copy of the conversation context, appends a memory-sync
    reminder, and calls the LLM once with only the memory_edit tool.
    The main conversation is never modified.
    """
    tools = [d for d in registry.get_definitions() if d.name == "memory_edit"]
    if not tools:
        return

    local_messages = builder.build(conversation)
    local_messages.append(
        Message(role="user", content=_build_memory_sync_reminder(missing_targets)),
    )

    with console.spinner():
        response = client.chat_with_tools(local_messages, tools)
    _debug_print_responder_output(console, response, label="memory-sync")

    for tool_call in response.tool_calls:
        if tool_call.name != "memory_edit":
            continue
        if not registry.has_tool(tool_call.name):
            continue
        console.print_tool_call(tool_call)
        if on_before_tool_call is not None:
            on_before_tool_call(tool_call)
        with console.spinner("Executing..."):
            result = registry.execute(tool_call)
        console.print_tool_result(tool_call, result)


def _run_memory_archive(agent_os_dir: Path, config: AppConfig, console: ChatConsole):
    """Run memory archive hook; log and swallow errors."""
    try:
        result = check_and_archive_buffers(agent_os_dir, config.hooks.memory_archive)
        if result.archived:
            console.print_info(f"Memory archived: {result.summary}")
    except Exception as e:
        logger.warning("Memory archive hook failed: %s", e)


def _run_memory_backup(backup_mgr: MemoryBackupManager | None):
    """Run periodic memory backup; log and swallow errors."""
    if backup_mgr is None:
        return
    try:
        backup_mgr.check_and_backup()
    except Exception as e:
        logger.warning("Memory backup failed: %s", e)


class AgentCore:
    """Core agent logic: responder + memory sync + post-review + shutdown."""

    def __init__(
        self,
        *,
        client: LLMClient,
        conversation: Conversation,
        builder: ContextBuilder,
        registry: ToolRegistry,
        console: ChatConsole,
        workspace: WorkspaceManager,
        config: AppConfig,
        agent_os_dir: Path,
        user_id: str,
        session_mgr: SessionManager | None = None,
        display_name: str = "",
        # Post-review
        post_reviewer: PostReviewer | None = None,
        post_max_retries: int = 2,
        post_allow_unresolved: bool = False,
        post_warn_on_failure: bool = True,
        post_review_packet_config: ReviewPacketConfig | None = None,
        # Progress review
        progress_reviewer: ProgressReviewer | None = None,
        progress_warn_on_failure: bool = True,
        # Shutdown review
        shutdown_reviewer: PostReviewer | None = None,
        shutdown_reviewer_max_retries: int = 0,
        shutdown_allow_unresolved: bool = False,
        shutdown_reviewer_warn_on_failure: bool = True,
        # Memory
        memory_edit_allow_failure: bool = False,
        memory_backup_mgr: MemoryBackupManager | None = None,
    ):
        self.client = client
        self.conversation = conversation
        self.builder = builder
        self.registry = registry
        self.console = console
        self.workspace = workspace
        self.config = config
        self.agent_os_dir = agent_os_dir
        self.user_id = user_id
        self.session_mgr = session_mgr
        self.display_name = display_name
        self.post_reviewer = post_reviewer
        self.post_max_retries = post_max_retries
        self.post_allow_unresolved = post_allow_unresolved
        self.post_warn_on_failure = post_warn_on_failure
        self.post_review_packet_config = post_review_packet_config or ReviewPacketConfig()
        self.progress_reviewer = progress_reviewer
        self.progress_warn_on_failure = progress_warn_on_failure
        self.shutdown_reviewer = shutdown_reviewer
        self.shutdown_reviewer_max_retries = shutdown_reviewer_max_retries
        self.shutdown_allow_unresolved = shutdown_allow_unresolved
        self.shutdown_reviewer_warn_on_failure = shutdown_reviewer_warn_on_failure
        self.memory_edit_allow_failure = memory_edit_allow_failure
        self.memory_backup_mgr = memory_backup_mgr
        self.has_new_user_content: bool = False

    def run_turn(self, user_input: str) -> None:
        """Process one user turn.

        Full lifecycle:
        1. Add user message to conversation
        2. Responder (LLM + tool loop)
        3. Memory sync side-channel
        4. Post-review retry loop
        5. Memory archive + backup hooks

        Handles ContextLengthExceededError (reduce preserve_turns + retry),
        KeyboardInterrupt (patch incomplete tool calls), and general exceptions
        (rollback memory + restore conversation).

        Output goes through self.console.
        """
        debug = self.console.debug
        pre_turn_anchor = len(self.conversation.get_messages())
        self.conversation.add("user", user_input)
        self.has_new_user_content = True
        messages = self.builder.build(self.conversation)

        # Start new session if context was truncated
        if self.builder.last_was_truncated:
            self.session_mgr.finalize("truncated")
            self.session_mgr.create(self.user_id, self.display_name)
            self.conversation._on_message = self.session_mgr.append_message

        turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
        turn_anchor = len(self.conversation.get_messages())

        esc_monitor = EscInterruptMonitor()
        try:
            esc_monitor.start()
            tools = self.registry.get_definitions()

            # === Responder ===
            response = _run_responder(
                self.client, messages, tools,
                self.conversation, self.builder, self.registry, self.console,
                on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                memory_edit_allow_failure=self.memory_edit_allow_failure,
                progress_reviewer=self.progress_reviewer,
                progress_review_warn_on_failure=self.progress_warn_on_failure,
            )
            final_content, used_fallback_content = _resolve_final_content(
                response.content,
                self.conversation.get_messages()[turn_anchor:],
            )

            # === Memory sync (side-channel, no conversation mutation) ===
            sync_turn_messages = self.conversation.get_messages()[turn_anchor:]
            missing_sync = find_missing_memory_sync_targets(sync_turn_messages)
            if missing_sync:
                if debug:
                    self.console.print_debug(
                        "memory-sync", f"missing: {', '.join(missing_sync)}"
                    )
                try:
                    _run_memory_sync_side_channel(
                        self.client, self.conversation, self.builder,
                        self.registry, self.console,
                        missing_targets=missing_sync,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                    )
                except ContextLengthExceededError:
                    if debug:
                        self.console.print_debug(
                            "memory-sync", "skipped: context length exceeded",
                        )
                except Exception:
                    if debug:
                        self.console.print_debug("memory-sync", "side-channel failed")

            # === Post-review pass ===
            if self.post_reviewer is not None:
                retry_count = 0
                last_action_signature: tuple[str, ...] | None = None
                fail_closed = False
                review_attempt_anchor = turn_anchor
                while True:
                    turn_messages = self.conversation.get_messages()[turn_anchor:]
                    has_visible_intermediate = _turn_has_visible_intermediate_text(
                        turn_messages
                    )
                    has_final_content = bool(final_content and final_content.strip())

                    # If user already saw intermediate text, do not force a duplicated final answer.
                    # Persist intermediate text so it survives resume.
                    if not has_final_content and has_visible_intermediate:
                        intermediate = _latest_intermediate_text(turn_messages)
                        if intermediate:
                            self.conversation.add("assistant", intermediate)
                        if debug:
                            self.console.print_debug(
                                "post-review",
                                "final response empty but intermediate text already shown; skipping final retry",
                            )
                        break

                    need_visible_reply_retry = not has_final_content and not has_visible_intermediate
                    actions_for_retry: list[RequiredAction] = []
                    retry_instruction = ""

                    if need_visible_reply_retry:
                        retry_instruction = "\u8acb\u63d0\u4f9b\u4e00\u6bb5\u7d66\u7528\u6236\u7684\u6700\u7d42\u56de\u8986\uff08\u4e0d\u53ef\u70ba\u7a7a\uff09\u3002"
                        if debug:
                            self.console.print_debug(
                                "post-review",
                                "no visible assistant reply in this turn; requesting final reply retry",
                            )
                    else:
                        review_messages = self.builder.build(self.conversation)
                        # Include the final text response in the packet so
                        # post-reviewer sees the actual candidate_assistant_reply.
                        # (_run_responder only adds tool-call messages to
                        # conversation, not the final text-only response.)
                        packet_messages = _build_post_review_packet_messages(
                            self.conversation.get_messages(),
                            turn_anchor=turn_anchor,
                            attempt_anchor=review_attempt_anchor,
                        )
                        if final_content and final_content.strip():
                            packet_messages = packet_messages + [
                                Message(role="assistant", content=final_content)
                            ]
                        review_packet = build_post_review_packet(
                            packet_messages,
                            turn_anchor=turn_anchor,
                            config=self.post_review_packet_config,
                        )
                        if debug:
                            truncated_sections = [
                                rec.section
                                for rec in review_packet.truncation_report
                            ]
                            self.console.print_debug(
                                "post-review packet",
                                "truncated_sections="
                                + (", ".join(truncated_sections) if truncated_sections else "(none)"),
                            )
                        elif review_packet.truncation_report and self.post_warn_on_failure:
                            self.console.print_warning("review_packet_truncated")
                        with self.console.spinner("Checking..."):
                            post_result = self.post_reviewer.review(
                                review_messages,
                                review_packet=review_packet,
                            )
                        if post_result is None and self.post_warn_on_failure:
                            self.console.print_warning(
                                _build_reviewer_warning(
                                    "Post-review",
                                    self.post_reviewer.last_raw_response,
                                    self.post_reviewer.last_error,
                                )
                            )
                        if post_result is None:
                            if debug:
                                raw = self.post_reviewer.last_raw_response or "(empty)"
                                self.console.print_debug_block(
                                    "post-review raw",
                                    _format_debug_json(raw),
                                )
                                self.console.print_debug("post-review", "parse failed, skipping")
                                if self.post_reviewer.last_error:
                                    self.console.print_debug(
                                        "post-review error",
                                        _sanitize_error_message(self.post_reviewer.last_error),
                                    )
                            break

                        retry_instruction = (
                            post_result.retry_instruction
                            or (post_result.guidance or "")
                        )
                        actions_for_retry = _collect_required_actions_for_retry(
                            turn_messages,
                            passed=post_result.passed,
                            required_actions=post_result.required_actions,
                        )
                        missing_actions = find_missing_actions(
                            turn_messages,
                            post_result.required_actions,
                        )
                        if post_result.required_actions and not missing_actions and debug:
                            self.console.print_debug(
                                "post-review",
                                "required actions already satisfied in this attempt; accepting",
                            )

                        turn_missing_memory_write = not _has_memory_write(turn_messages)
                        if turn_missing_memory_write:
                            actions_for_retry = _ensure_turn_persistence_action(actions_for_retry)
                            if not retry_instruction:
                                retry_instruction = (
                                    "Persist this turn to memory before final answer."
                                )

                        if debug:
                            raw = self.post_reviewer.last_raw_response or "(empty)"
                            self.console.print_debug_block(
                                "post-review raw", _format_debug_json(raw),
                            )
                            for action in actions_for_retry:
                                self.console.print_debug(
                                    "post-review action",
                                    f"{action.code} | tool={action.tool} | "
                                    f"path={action.target_path or action.target_path_glob or '-'}",
                                )
                            if retry_instruction:
                                self.console.print_debug(
                                    "post-review instruction",
                                    retry_instruction,
                                )
                            if post_result.passed and not actions_for_retry:
                                self.console.print_debug("post-review", "PASS")
                            else:
                                self.console.print_debug("post-review", "FAIL")

                        if post_result.passed and not actions_for_retry:
                            break
                        if not actions_for_retry:
                            if debug:
                                self.console.print_debug(
                                    "post-review",
                                    "no required actions requested; accepting current reply",
                                )
                            break

                    signature_markers = (
                        ["missing_visible_reply"] if need_visible_reply_retry else []
                    )
                    signature = _action_signature(
                        actions_for_retry,
                        signature_markers,
                    )
                    if signature and signature == last_action_signature:
                        if need_visible_reply_retry:
                            if self.post_warn_on_failure:
                                self.console.print_warning(
                                    "Post-review could not obtain a visible final reply; fail-closed."
                                )
                            fail_closed = True
                            break
                        if self.post_allow_unresolved:
                            self.console.print_warning(
                                "Post-review detected repeated unresolved actions; "
                                "allow_unresolved=true, sending reply with warning."
                            )
                            break
                        if self.post_warn_on_failure:
                            self.console.print_warning(
                                "Post-review detected repeated unresolved actions; fail-closed."
                            )
                        if debug:
                            self.console.print_debug(
                                "post-review",
                                "same retry signature repeated, fail-closed",
                            )
                        fail_closed = True
                        break
                    last_action_signature = signature

                    if retry_count >= self.post_max_retries:
                        if need_visible_reply_retry:
                            if self.post_warn_on_failure:
                                self.console.print_warning(
                                    "Post-review could not obtain a visible final reply after max retries; fail-closed."
                                )
                            fail_closed = True
                            break
                        if self.post_allow_unresolved:
                            self.console.print_warning(
                                "Post-review found unresolved actions after max retries; "
                                "allow_unresolved=true, sending reply with warning."
                            )
                            break
                        if self.post_warn_on_failure:
                            self.console.print_warning(
                                "Post-review found unresolved actions after max retries; fail-closed."
                            )
                        fail_closed = True
                        break

                    retry_count += 1
                    if debug:
                        self.console.print_debug("post-review", f"retry {retry_count}/{self.post_max_retries}")

                    # Keep previous tool calls/results in conversation so the
                    # brain sees its prior work (e.g. boot) and doesn't redo it.
                    # Inject as synthetic tool call + result to avoid mutating
                    # system_instruction (which invalidates prompt cache on
                    # OpenRouter/Gemini).
                    if need_visible_reply_retry:
                        directive = _build_missing_visible_reply_directive(
                            retry_instruction=retry_instruction,
                            attempt=retry_count,
                            max_attempts=self.post_max_retries,
                        )
                    else:
                        directive = _build_retry_directive(
                            required_actions=actions_for_retry,
                            retry_instruction=retry_instruction,
                            attempt=retry_count,
                            max_attempts=self.post_max_retries,
                        )
                    retry_tool_id = f"retry-{uuid.uuid4().hex[:8]}"
                    self.conversation.add_assistant_with_tools(
                        None,
                        [ToolCall(id=retry_tool_id, name="_post_review", arguments={})],
                    )
                    self.conversation.add_tool_result(retry_tool_id, "_post_review", directive)
                    review_attempt_anchor = len(self.conversation.get_messages())
                    messages = self.builder.build(self.conversation)
                    response = _run_responder(
                        self.client, messages, tools,
                        self.conversation, self.builder, self.registry, self.console,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        memory_edit_allow_failure=self.memory_edit_allow_failure,
                        progress_reviewer=self.progress_reviewer,
                        progress_review_warn_on_failure=self.progress_warn_on_failure,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        self.conversation.get_messages()[turn_anchor:],
                    )
                if fail_closed:
                    self.conversation._messages = self.conversation._messages[:turn_anchor]
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot,
                        console=self.console,
                        debug=debug,
                    )
                    self.console.print_error(
                        "Post-review unresolved (fail-closed); no assistant reply was sent."
                    )
                    return
                if final_content and not used_fallback_content:
                    self.conversation.add("assistant", final_content)
                if not used_fallback_content:
                    self.console.print_assistant(final_content)
            else:
                if final_content and not used_fallback_content:
                    self.conversation.add("assistant", final_content)
                elif not final_content:
                    # Persist intermediate text for resume when no final reply.
                    turn_msgs = self.conversation.get_messages()[turn_anchor:]
                    intermediate = _latest_intermediate_text(turn_msgs)
                    if intermediate:
                        self.conversation.add("assistant", intermediate)
                if not used_fallback_content:
                    self.console.print_assistant(final_content)

            # Post-turn hooks
            _run_memory_archive(self.agent_os_dir, self.config, self.console)
            _run_memory_backup(self.memory_backup_mgr)

        except ContextLengthExceededError:
            _rollback_turn_memory_changes(
                turn_memory_snapshot, console=self.console, debug=debug,
            )
            self.conversation._messages = self.conversation._messages[:pre_turn_anchor]

            # Archive before retry to shrink boot files (e.g. short-term.md)
            _run_memory_archive(self.agent_os_dir, self.config, self.console)

            # Retry with progressively fewer turns:
            # Always reduce preserve_turns first to make room for tool results,
            # avoiding the LLM re-executing the same tool calls that caused overflow.
            _min_preserve = 2
            while True:
                if self.builder.preserve_turns <= _min_preserve:
                    self.console.print_error(
                        "Context still too large after reducing to minimum turns."
                    )
                    break
                self.builder.preserve_turns = max(
                    _min_preserve, self.builder.preserve_turns // 2,
                )
                self.console.print_warning(
                    f"Token limit exceeded. "
                    f"Reducing preserve_turns to {self.builder.preserve_turns}, retrying..."
                )

                self.conversation.add("user", user_input)
                messages = self.builder.build(self.conversation)
                turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
                try:
                    tools = self.registry.get_definitions()
                    turn_anchor = len(self.conversation.get_messages())
                    response = _run_responder(
                        self.client, messages, tools,
                        self.conversation, self.builder, self.registry, self.console,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        memory_edit_allow_failure=self.memory_edit_allow_failure,
                        progress_reviewer=self.progress_reviewer,
                        progress_review_warn_on_failure=self.progress_warn_on_failure,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        self.conversation.get_messages()[turn_anchor:],
                    )
                    if final_content and not used_fallback_content:
                        self.conversation.add("assistant", final_content)
                    if not used_fallback_content:
                        self.console.print_assistant(final_content)
                    _run_memory_archive(self.agent_os_dir, self.config, self.console)
                    _run_memory_backup(self.memory_backup_mgr)
                    break
                except ContextLengthExceededError:
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot, console=self.console, debug=debug,
                    )
                    self.conversation._messages = self.conversation._messages[:pre_turn_anchor]
                    continue
                except Exception as e:
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot, console=self.console, debug=debug,
                    )
                    self.console.print_error(_sanitize_error_message(str(e)))
                    self.conversation._messages = self.conversation._messages[:pre_turn_anchor]
                    break

        except KeyboardInterrupt:
            # Preserve completed work; patch incomplete tool calls for API consistency
            _patch_interrupted_tool_calls(self.conversation, turn_anchor)
            self.session_mgr.rewrite_messages(self.conversation.get_messages())
            self.console.print_info("Interrupted.")
            return

        except Exception as e:
            _rollback_turn_memory_changes(
                turn_memory_snapshot,
                console=self.console,
                debug=debug,
            )
            self.console.print_error(_sanitize_error_message(str(e)))
            self.conversation._messages = self.conversation._messages[:pre_turn_anchor]
            return

        finally:
            esc_monitor.stop()

    def graceful_exit(self) -> None:
        """Handle graceful exit with optional memory saving."""
        # Finalize session before shutdown flow so shutdown messages
        # are not persisted into the session file.
        if self.session_mgr is not None:
            self.session_mgr.finalize("completed")

        if self.has_new_user_content and _has_conversation_content(self.conversation):
            try:
                shutdown_ok = perform_shutdown(
                    self.client, self.conversation, self.builder, self.registry,
                    self.console, self.workspace, self.user_id,
                    reviewer=self.shutdown_reviewer,
                    reviewer_max_retries=self.shutdown_reviewer_max_retries,
                    reviewer_allow_unresolved=self.shutdown_allow_unresolved,
                    reviewer_warn_on_failure=self.shutdown_reviewer_warn_on_failure,
                    memory_edit_allow_failure=self.memory_edit_allow_failure,
                )
                if not shutdown_ok:
                    self.console.print_error(
                        "Shutdown memory persistence failed (fail-closed)."
                    )
            except KeyboardInterrupt:
                self.console.print_info("Shutdown interrupted.")
            except Exception as e:
                self.console.print_error(f"Failed to save memories: {e}")
        # Archive oversized buffers after shutdown writes
        if self.agent_os_dir and self.config:
            _run_memory_archive(self.agent_os_dir, self.config, self.console)
            # Clean up expired sessions
            if self.config.hooks.session_cleanup.enabled:
                try:
                    from ..session.cleanup import cleanup_sessions
                    cleanup_sessions(
                        self.agent_os_dir / "session",
                        retention_days=self.config.hooks.session_cleanup.retention_days,
                    )
                except Exception:
                    logger.warning("Session cleanup failed")
        _run_memory_backup(self.memory_backup_mgr)
        self.console.print_goodbye()
