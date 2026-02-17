from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import json
import re
import uuid

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import AppConfig, ToolsConfig
from ..llm import LLMResponse, create_client
from ..llm.base import LLMClient
from ..llm.schema import ContextLengthExceededError, Message, ToolCall, ToolDefinition
from ..memory import (
    MemoryEditor,
    MemoryEditPlanner,
    SessionCommitLog,
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
from ..workspace import WorkspaceManager, WorkspaceInitializer
from ..workspace.people import ensure_user_memory_file, resolve_user_selector
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
    GUISessionStore,
    GUIWorker,
    SCREENSHOT_DEFINITION,
    create_gui_task,
    create_screenshot,
)
from prompt_toolkit.formatted_text import HTML

from .console import ChatConsole
from .input import ChatInput
from .interrupt import EscInterruptMonitor
from .picker import pick_one
from .commands import CommandHandler, CommandResult
from .shutdown import perform_shutdown, _has_conversation_content
from ..session import SessionManager, pick_session

class _DebugConsoleHandler(logging.Handler):
    """Route log records to ChatConsole.print_debug."""

    def __init__(self, console: "ChatConsole"):
        super().__init__()
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        self._console.print_debug("llm-retry", self.format(record))


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
    """Build directive for missing memory sync targets.

    Injected as a synthetic user message so the model treats it as
    an instruction rather than informational tool output.
    """
    targets = "\n".join(f"- {t}" for t in missing_targets)
    return (
        "[MEMORY SYNC — system generated, not user input]\n"
        f"You have not updated the following files this turn:\n{targets}\n"
        "Call memory_edit now to update them.\n"
        "Do not mention this reminder in your reply."
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

    # Image tool — uses the same allowed_paths as other file tools.
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

    # Screenshot tool — direct screenshot for brain's vision model
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


def _graceful_exit(
    client,
    conversation,
    builder,
    registry,
    console,
    workspace,
    user_id,
    agent_os_dir: Path | None = None,
    config: AppConfig | None = None,
    shutdown_reviewer=None,
    shutdown_reviewer_max_retries: int = 0,
    shutdown_allow_unresolved: bool = False,
    shutdown_reviewer_warn_on_failure: bool = True,
    memory_edit_allow_failure: bool = False,
    session_mgr: SessionManager | None = None,
    has_new_user_content: bool = False,
    memory_backup_mgr: MemoryBackupManager | None = None,
):
    """Handle graceful exit with optional memory saving."""
    # Finalize session before shutdown flow so shutdown messages
    # are not persisted into the session file.
    if session_mgr is not None:
        session_mgr.finalize("completed")

    if has_new_user_content and _has_conversation_content(conversation):
        try:
            shutdown_ok = perform_shutdown(
                client, conversation, builder, registry,
                console, workspace, user_id,
                reviewer=shutdown_reviewer,
                reviewer_max_retries=shutdown_reviewer_max_retries,
                reviewer_allow_unresolved=shutdown_allow_unresolved,
                reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
                memory_edit_allow_failure=memory_edit_allow_failure,
            )
            if not shutdown_ok:
                console.print_error(
                    "Shutdown memory persistence failed (fail-closed)."
                )
        except KeyboardInterrupt:
            console.print_info("Shutdown interrupted.")
        except Exception as e:
            console.print_error(f"Failed to save memories: {e}")
    # Archive oversized buffers after shutdown writes
    if agent_os_dir and config:
        _run_memory_archive(agent_os_dir, config, console)
        # Clean up expired sessions
        if config.hooks.session_cleanup.enabled:
            try:
                from ..session.cleanup import cleanup_sessions
                cleanup_sessions(
                    agent_os_dir / "session",
                    retention_days=config.hooks.session_cleanup.retention_days,
                )
            except Exception:
                logger.warning("Session cleanup failed")
    _run_memory_backup(memory_backup_mgr)
    console.print_goodbye()


def main(user: str, resume: str | None = None) -> None:
    """Main entry point for the CLI."""
    user_selector = user.strip()
    if not user_selector:
        raise ValueError("user is required")

    config = load_config()
    agent_os_dir = config.get_agent_os_dir()

    # Check workspace initialization
    workspace = WorkspaceManager(agent_os_dir)
    console = ChatConsole()

    if not workspace.is_initialized():
        console.print_error(f"Workspace not initialized at {agent_os_dir}")
        console.print_info("Run 'uv run python -m chat_agent init' first.")
        return

    # Auto-upgrade kernel if needed
    initializer = WorkspaceInitializer(workspace)
    if initializer.needs_upgrade():
        console.print_info("Upgrading kernel...")
        applied = initializer.upgrade_kernel()
        for v in applied:
            console.print_info(f"  Applied: v{v}")
        console.print_info("Kernel upgraded.")

    try:
        user_id, display_name = resolve_user_selector(workspace.memory_dir, user_selector)
        ensure_user_memory_file(workspace.memory_dir, user_id, display_name)
    except ValueError as e:
        console.print_error(str(e))
        return

    # Load bootloader prompt and resolve {agent_os_dir} placeholder
    try:
        system_prompt = workspace.get_system_prompt("brain")
        system_prompt = system_prompt.replace("{agent_os_dir}", str(agent_os_dir))
    except FileNotFoundError as e:
        console.print_error(f"Failed to load system prompt: {e}")
        return

    debug = config.debug
    console.set_debug(debug)
    console.set_show_tool_use(config.show_tool_use)
    global_warn_on_failure = config.warn_on_failure

    # Bridge retry logger to debug console output.
    if debug:
        _retry_logger = logging.getLogger("chat_agent.llm.retry")
        _retry_handler = _DebugConsoleHandler(console)
        _retry_handler.setLevel(logging.DEBUG)
        _retry_logger.addHandler(_retry_handler)
        _retry_logger.setLevel(logging.DEBUG)

    agent_hint = config.features.copilot_agent_hint

    brain_agent_config = config.agents["brain"]
    client = create_client(
        brain_agent_config.llm,
        timeout_retries=brain_agent_config.llm_timeout_retries,
        request_timeout=brain_agent_config.llm_request_timeout,
        rate_limit_retries=brain_agent_config.llm_429_retries,
    )

    if "memory_editor" not in config.agents:
        console.print_error("Missing required agent config: agents.memory_editor")
        return

    memory_editor_config = config.agents["memory_editor"]
    if not memory_editor_config.enabled:
        console.print_error("agents.memory_editor must be enabled.")
        return

    memory_editor_client = create_client(
        memory_editor_config.llm,
        timeout_retries=memory_editor_config.llm_timeout_retries,
        request_timeout=memory_editor_config.llm_request_timeout,
        rate_limit_retries=memory_editor_config.llm_429_retries,
        force_agent=agent_hint,
    )

    try:
        memory_editor_prompt = workspace.get_system_prompt("memory_editor")
    except FileNotFoundError as e:
        console.print_error(f"Failed to load memory_editor prompt: {e}")
        return

    memory_editor_parse_retry: str | None = None
    try:
        memory_editor_parse_retry = workspace.get_agent_prompt(
            "memory_editor",
            "parse-retry",
            current_user=user_id,
        )
    except FileNotFoundError:
        pass

    memory_planner = MemoryEditPlanner(
        memory_editor_client,
        memory_editor_prompt,
        parse_retries=memory_editor_config.post_parse_retries,
        parse_retry_prompt=memory_editor_parse_retry,
    )
    memory_editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=memory_planner,
    )

    timezone = workspace.get_timezone()

    # Session persistence
    session_mgr = SessionManager(agent_os_dir / "session" / "brain")
    has_new_user_content = False

    resume_id: str | None = None
    if resume is not None:
        # Resume flow
        if resume == "__continue__":
            sessions = session_mgr.list_recent(user_id=user_id, limit=1)
            if sessions:
                resume_id = sessions[0].session_id
        elif resume == "":
            sessions = session_mgr.list_recent(user_id=user_id)
            selected = pick_session(sessions)
            if not selected:
                return
            resume_id = selected.session_id
        else:
            resume_id = resume

    if resume_id is not None:
        messages = session_mgr.load(resume_id)
        conversation = Conversation(on_message=session_mgr.append_message)
        conversation._messages = messages  # Restore without triggering callback
        console.print_info(
            f"Resumed session {resume_id} ({len(messages)} messages)"
        )
    else:
        session_mgr.create(user_id, display_name)
        conversation = Conversation(on_message=session_mgr.append_message)

    builder = ContextBuilder(
        system_prompt=system_prompt,
        timezone=timezone,
        current_user=user_id,
        agent_os_dir=agent_os_dir,
        boot_files=config.context.boot_files,
        max_chars=config.context.max_chars,
        preserve_turns=config.context.preserve_turns,
        provider=brain_agent_config.llm.provider,
    )

    def _context_toolbar():
        chars = builder.last_total_chars
        limit = builder.max_chars
        pct = (chars / limit * 100) if limit else 0
        return HTML(
            f"<style fg='#888888'>ctx: {chars:,} / {limit:,} ({pct:.1f}%)</style>"
        )

    chat_input = ChatInput(timezone=timezone, bottom_toolbar=_context_toolbar)
    # Optional memory search agent
    memory_search_agent = None
    if "memory_searcher" in config.agents and config.agents["memory_searcher"].enabled:
        ms_config = config.agents["memory_searcher"]
        ms_client = create_client(
            ms_config.llm,
            timeout_retries=ms_config.llm_timeout_retries,
            request_timeout=ms_config.llm_request_timeout,
            rate_limit_retries=ms_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            ms_prompt = workspace.get_system_prompt("memory_searcher")
            ms_parse_retry: str | None = None
            try:
                ms_parse_retry = workspace.get_agent_prompt(
                    "memory_searcher", "parse-retry", current_user=user_id,
                )
            except FileNotFoundError:
                pass
            memory_search_agent = MemorySearchAgent(
                ms_client,
                ms_prompt,
                memory_dir=agent_os_dir / "memory",
                parse_retries=ms_config.pre_parse_retries,
                parse_retry_prompt=ms_parse_retry,
                context_bytes_limit=ms_config.context_bytes_limit,
                max_results=ms_config.max_results,
            )
        except FileNotFoundError:
            pass

    # Vision agent initialization
    brain_has_vision = bool(
        brain_agent_config.llm.capabilities
        and brain_agent_config.llm.capabilities.vision
    )
    _use_own_vision = brain_agent_config.use_own_vision_ability
    vision_agent_instance: VisionAgent | None = None
    if (not brain_has_vision or not _use_own_vision) and "vision" in config.agents and config.agents["vision"].enabled:
        vision_config = config.agents["vision"]
        vision_client = create_client(
            vision_config.llm,
            timeout_retries=vision_config.llm_timeout_retries,
            request_timeout=vision_config.llm_request_timeout,
            rate_limit_retries=vision_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            vision_prompt = workspace.get_system_prompt("vision")
            vision_agent_instance = VisionAgent(vision_client, vision_prompt)
        except FileNotFoundError:
            pass

    # GUI automation agent initialization
    gui_manager_instance: GUIManager | None = None
    if "gui_manager" in config.agents and config.agents["gui_manager"].enabled:
        gm_config = config.agents["gui_manager"]
        gm_client = create_client(
            gm_config.llm,
            timeout_retries=gm_config.llm_timeout_retries,
            request_timeout=gm_config.llm_request_timeout,
            rate_limit_retries=gm_config.llm_429_retries,
            force_agent=agent_hint,
        )
        gw_config = config.agents.get("gui_worker")
        if gw_config and gw_config.enabled:
            gw_client = create_client(
                gw_config.llm,
                timeout_retries=gw_config.llm_timeout_retries,
                request_timeout=gw_config.llm_request_timeout,
                rate_limit_retries=gw_config.llm_429_retries,
                force_agent=agent_hint,
            )
            try:
                gm_prompt = workspace.get_system_prompt("gui_manager")
                gw_prompt = workspace.get_system_prompt("gui_worker")
                gw_layout_prompt = workspace.get_agent_prompt("gui_worker", "layout")
                worker = GUIWorker(
                    gw_client, gw_prompt,
                    screenshot_max_width=gm_config.screenshot_max_width,
                    screenshot_quality=gm_config.screenshot_quality,
                    layout_prompt=gw_layout_prompt,
                )
                gui_session_store = GUISessionStore(agent_os_dir / "session" / "gui")

                def _gui_step_callback(
                    tool_call, result, step, max_steps,
                    elapsed_sec, total_elapsed_sec, worker_timing,
                ):
                    console.print_gui_step(
                        tool_call, result, step, max_steps,
                        elapsed_sec, total_elapsed_sec,
                        worker_timing=worker_timing,
                        instruction_max_chars=gm_config.gui_instruction_max_chars,
                        text_max_chars=gm_config.gui_text_max_chars,
                        worker_result_max_chars=gm_config.gui_worker_result_max_chars,
                        result_max_chars=gm_config.gui_result_max_chars,
                    )

                console.gui_intent_max_chars = gm_config.gui_intent_max_chars
                gui_manager_instance = GUIManager(
                    gm_client, worker, gm_prompt,
                    max_steps=gm_config.max_steps,
                    session_store=gui_session_store,
                    on_step=_gui_step_callback,
                    screenshot_max_width=gm_config.screenshot_max_width,
                    screenshot_quality=gm_config.screenshot_quality,
                    scroll_invert=config.tools.scroll.invert,
                    scroll_max_amount=config.tools.scroll.max_amount,
                )
            except FileNotFoundError:
                pass

    # Screenshot settings (from gui_manager config if available)
    _gm_cfg = config.agents.get("gui_manager")
    _ss_max_width = _gm_cfg.screenshot_max_width if _gm_cfg else 1280
    _ss_quality = _gm_cfg.screenshot_quality if _gm_cfg else 80

    registry = setup_tools(
        config.tools,
        agent_os_dir,
        memory_editor=memory_editor,
        memory_search_agent=memory_search_agent,
        brain_has_vision=brain_has_vision,
        use_own_vision_ability=_use_own_vision,
        vision_agent=vision_agent_instance,
        gui_manager=gui_manager_instance,
        screenshot_max_width=_ss_max_width,
        screenshot_quality=_ss_quality,
    )
    memory_edit_allow_failure = config.tools.memory_edit.allow_failure
    commands = CommandHandler(console)

    post_reviewer = None
    post_max_retries = 2
    post_allow_unresolved = False
    post_warn_on_failure = True
    post_review_packet_config = ReviewPacketConfig()
    post_config = config.agents.get("post_reviewer")

    if post_config is not None and post_config.enabled:
        post_max_retries = post_config.max_post_retries
        post_allow_unresolved = post_config.allow_unresolved
        post_warn_on_failure = global_warn_on_failure and post_config.warn_on_failure
        post_review_packet_config = ReviewPacketConfig(
            history_turns=post_config.history_turns,
            history_turn_max_chars=post_config.history_turn_max_chars,
            reply_max_chars=post_config.reply_max_chars,
            tool_preview_max_chars=post_config.tool_preview_max_chars,
        )
        post_client = create_client(
            post_config.llm,
            timeout_retries=post_config.llm_timeout_retries,
            request_timeout=post_config.llm_request_timeout,
            rate_limit_retries=post_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            post_prompt = workspace.get_system_prompt("post_reviewer")
            post_parse_retry_prompt: str | None = None
            try:
                post_parse_retry_prompt = workspace.get_agent_prompt(
                    "post_reviewer",
                    "parse-retry",
                    current_user=user_id,
                )
            except FileNotFoundError:
                pass
            post_reviewer = PostReviewer(
                post_client,
                post_prompt,
                parse_retries=post_config.post_parse_retries,
                parse_retry_prompt=post_parse_retry_prompt,
            )
        except FileNotFoundError as e:
            raise SystemExit(
                f"Config error: missing required post_reviewer prompt ({e})"
            )

    progress_reviewer = None
    progress_warn_on_failure = True
    progress_config = config.agents.get("progress_reviewer")
    if progress_config is not None and progress_config.enabled:
        progress_warn_on_failure = (
            global_warn_on_failure and progress_config.warn_on_failure
        )
        progress_client = create_client(
            progress_config.llm,
            timeout_retries=progress_config.llm_timeout_retries,
            request_timeout=progress_config.llm_request_timeout,
            rate_limit_retries=progress_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            progress_prompt = workspace.get_system_prompt("progress_reviewer")
            progress_parse_retry_prompt: str | None = None
            try:
                progress_parse_retry_prompt = workspace.get_agent_prompt(
                    "progress_reviewer",
                    "parse-retry",
                    current_user=user_id,
                )
            except FileNotFoundError:
                pass
            progress_reviewer = ProgressReviewer(
                progress_client,
                progress_prompt,
                parse_retries=progress_config.post_parse_retries,
                parse_retry_prompt=progress_parse_retry_prompt,
            )
        except FileNotFoundError as e:
            raise SystemExit(
                f"Config error: missing required progress_reviewer prompt ({e})"
            )

    shutdown_reviewer = None
    shutdown_reviewer_max_retries = 0
    shutdown_allow_unresolved = False
    shutdown_reviewer_warn_on_failure = True
    if "shutdown_reviewer" in config.agents and config.agents["shutdown_reviewer"].enabled:
        shutdown_config = config.agents["shutdown_reviewer"]
        shutdown_reviewer_max_retries = shutdown_config.max_post_retries
        shutdown_allow_unresolved = shutdown_config.allow_unresolved
        shutdown_reviewer_warn_on_failure = (
            global_warn_on_failure and shutdown_config.warn_on_failure
        )
        shutdown_client = create_client(
            shutdown_config.llm,
            timeout_retries=shutdown_config.llm_timeout_retries,
            request_timeout=shutdown_config.llm_request_timeout,
            rate_limit_retries=shutdown_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            shutdown_prompt = workspace.get_system_prompt("shutdown_reviewer")
            shutdown_parse_retry_prompt: str | None = None
            try:
                shutdown_parse_retry_prompt = workspace.get_agent_prompt(
                    "shutdown_reviewer",
                    "parse-retry",
                    current_user=user_id,
                )
            except FileNotFoundError:
                pass
            shutdown_reviewer = PostReviewer(
                shutdown_client,
                shutdown_prompt,
                parse_retries=shutdown_config.post_parse_retries,
                parse_retry_prompt=shutdown_parse_retry_prompt,
            )
        except FileNotFoundError:
            pass

    if resume is not None:
        console.print_resume_history(
            conversation.get_messages(),
            replay_turns=config.session.replay_turns,
            show_tool_calls=config.session.show_tool_calls,
            timezone=timezone,
        )
        # Warm up builder so ctx counter in toolbar is accurate.
        builder.build(conversation)

    # Periodic memory backup
    memory_backup_mgr = None
    if config.hooks.memory_backup.enabled:
        memory_backup_mgr = MemoryBackupManager(agent_os_dir, config.hooks.memory_backup)

    if resume is None:
        console.print_welcome()

    while True:
        user_input = chat_input.get_input()

        if user_input is None:
            _graceful_exit(
                client, conversation, builder, registry,
                console, workspace, user_id,
                agent_os_dir=agent_os_dir,
                config=config,
                shutdown_reviewer=shutdown_reviewer,
                shutdown_reviewer_max_retries=shutdown_reviewer_max_retries,
                shutdown_allow_unresolved=shutdown_allow_unresolved,
                shutdown_reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
                memory_edit_allow_failure=memory_edit_allow_failure,
                session_mgr=session_mgr,
                has_new_user_content=has_new_user_content,
                memory_backup_mgr=memory_backup_mgr,
            )
            break

        # Double ESC: interactive rollback picker
        if chat_input.wants_history_select:
            msgs = conversation.get_messages()
            user_turns = [(i, m) for i, m in enumerate(msgs) if m.role == "user"]
            if not user_turns:
                continue
            recent = user_turns[-10:]
            items = []
            for _idx, m in recent:
                preview = (m.content or "")[:60].replace("\n", " ")
                if len(m.content or "") > 60:
                    preview += "..."
                items.append(preview)
            choice = pick_one(items, title="\u9078\u64c7\u8981\u56de\u9000\u5230\u7684\u8f38\u5165\uff1a")
            if choice is not None:
                selected_idx, selected_msg = recent[choice]
                prev_input = selected_msg.content or ""
                conversation._messages = conversation._messages[:selected_idx]
                session_mgr.rewrite_messages(conversation.get_messages())
                chat_input.set_prefill(prev_input)
                console.print_info("\u5df2\u56de\u9000\u3002")
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if commands.is_command(user_input):
            result = commands.execute(user_input)
            if result == CommandResult.SHUTDOWN:
                _graceful_exit(
                    client, conversation, builder, registry,
                    console, workspace, user_id,
                    agent_os_dir=agent_os_dir,
                    config=config,
                    shutdown_reviewer=shutdown_reviewer,
                    shutdown_reviewer_max_retries=shutdown_reviewer_max_retries,
                    shutdown_allow_unresolved=shutdown_allow_unresolved,
                    shutdown_reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
                    memory_edit_allow_failure=memory_edit_allow_failure,
                    session_mgr=session_mgr,
                    has_new_user_content=has_new_user_content,
                    memory_backup_mgr=memory_backup_mgr,
                )
                break
            elif result == CommandResult.EXIT:
                session_mgr.finalize("exited")
                console.print_goodbye()
                break
            elif result == CommandResult.CLEAR:
                conversation.clear()
            elif result == CommandResult.COMPACT:
                removed = conversation.compact(builder.preserve_turns)
                if removed:
                    session_mgr.finalize("compacted")
                    session_mgr.create(user_id, display_name)
                    conversation._on_message = session_mgr.append_message
                    console.print_info(f"Context compacted: {removed} messages removed.")
                else:
                    console.print_info("Context is already compact.")
            elif result == CommandResult.RELOAD_SYSTEM_PROMPT:
                try:
                    reloaded = workspace.get_system_prompt("brain")
                    builder.system_prompt = reloaded.replace(
                        "{agent_os_dir}", str(agent_os_dir)
                    )
                    console.print_info("System prompt reloaded.")
                except FileNotFoundError as e:
                    console.print_error(f"Failed to reload system prompt: {e}")
            continue

        pre_turn_anchor = len(conversation.get_messages())
        conversation.add("user", user_input)
        has_new_user_content = True
        messages = builder.build(conversation)

        # Start new session if context was truncated
        if builder.last_was_truncated:
            session_mgr.finalize("truncated")
            session_mgr.create(user_id, display_name)
            conversation._on_message = session_mgr.append_message

        turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=agent_os_dir)
        turn_anchor = len(conversation.get_messages())

        esc_monitor = EscInterruptMonitor()
        try:
            esc_monitor.start()
            tools = registry.get_definitions()

            # === Responder ===
            response = _run_responder(
                client, messages, tools,
                conversation, builder, registry, console,
                on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                memory_edit_allow_failure=memory_edit_allow_failure,
                progress_reviewer=progress_reviewer,
                progress_review_warn_on_failure=progress_warn_on_failure,
            )
            final_content, used_fallback_content = _resolve_final_content(
                response.content,
                conversation.get_messages()[turn_anchor:],
            )

            # === Memory sync reminder (one-shot) ===
            sync_turn_messages = conversation.get_messages()[turn_anchor:]
            missing_sync = find_missing_memory_sync_targets(sync_turn_messages)
            if missing_sync:
                if debug:
                    console.print_debug(
                        "memory-sync", f"missing: {', '.join(missing_sync)}"
                    )
                conversation.add(
                    "user", _build_memory_sync_reminder(missing_sync),
                )
                messages = builder.build(conversation)
                response = _run_responder(
                    client, messages, tools,
                    conversation, builder, registry, console,
                    on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                    memory_edit_allow_failure=memory_edit_allow_failure,
                    progress_reviewer=progress_reviewer,
                    progress_review_warn_on_failure=progress_warn_on_failure,
                )
                final_content, used_fallback_content = _resolve_final_content(
                    response.content,
                    conversation.get_messages()[turn_anchor:],
                )

            # === Post-review pass ===
            if post_reviewer is not None:
                retry_count = 0
                last_action_signature: tuple[str, ...] | None = None
                fail_closed = False
                review_attempt_anchor = turn_anchor
                while True:
                    turn_messages = conversation.get_messages()[turn_anchor:]
                    has_visible_intermediate = _turn_has_visible_intermediate_text(
                        turn_messages
                    )
                    has_final_content = bool(final_content and final_content.strip())

                    # If user already saw intermediate text, do not force a duplicated final answer.
                    # Persist intermediate text so it survives resume.
                    if not has_final_content and has_visible_intermediate:
                        intermediate = _latest_intermediate_text(turn_messages)
                        if intermediate:
                            conversation.add("assistant", intermediate)
                        if debug:
                            console.print_debug(
                                "post-review",
                                "final response empty but intermediate text already shown; skipping final retry",
                            )
                        break

                    need_visible_reply_retry = not has_final_content and not has_visible_intermediate
                    actions_for_retry: list[RequiredAction] = []
                    retry_instruction = ""

                    if need_visible_reply_retry:
                        retry_instruction = "請提供一段給用戶的最終回覆（不可為空）。"
                        if debug:
                            console.print_debug(
                                "post-review",
                                "no visible assistant reply in this turn; requesting final reply retry",
                            )
                    else:
                        review_messages = builder.build(conversation)
                        # Include the final text response in the packet so
                        # post-reviewer sees the actual candidate_assistant_reply.
                        # (_run_responder only adds tool-call messages to
                        # conversation, not the final text-only response.)
                        packet_messages = _build_post_review_packet_messages(
                            conversation.get_messages(),
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
                            config=post_review_packet_config,
                        )
                        if debug:
                            truncated_sections = [
                                rec.section
                                for rec in review_packet.truncation_report
                            ]
                            console.print_debug(
                                "post-review packet",
                                "truncated_sections="
                                + (", ".join(truncated_sections) if truncated_sections else "(none)"),
                            )
                        elif review_packet.truncation_report and post_warn_on_failure:
                            console.print_warning("review_packet_truncated")
                        with console.spinner("Checking..."):
                            post_result = post_reviewer.review(
                                review_messages,
                                review_packet=review_packet,
                            )
                        if post_result is None and post_warn_on_failure:
                            console.print_warning(
                                _build_reviewer_warning(
                                    "Post-review",
                                    post_reviewer.last_raw_response,
                                    post_reviewer.last_error,
                                )
                            )
                        if post_result is None:
                            if debug:
                                raw = post_reviewer.last_raw_response or "(empty)"
                                console.print_debug_block(
                                    "post-review raw",
                                    _format_debug_json(raw),
                                )
                                console.print_debug("post-review", "parse failed, skipping")
                                if post_reviewer.last_error:
                                    console.print_debug(
                                        "post-review error",
                                        _sanitize_error_message(post_reviewer.last_error),
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
                            console.print_debug(
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
                            raw = post_reviewer.last_raw_response or "(empty)"
                            console.print_debug_block(
                                "post-review raw", _format_debug_json(raw),
                            )
                            for action in actions_for_retry:
                                console.print_debug(
                                    "post-review action",
                                    f"{action.code} | tool={action.tool} | "
                                    f"path={action.target_path or action.target_path_glob or '-'}",
                                )
                            if retry_instruction:
                                console.print_debug(
                                    "post-review instruction",
                                    retry_instruction,
                                )
                            if post_result.passed and not actions_for_retry:
                                console.print_debug("post-review", "PASS")
                            else:
                                console.print_debug("post-review", "FAIL")

                        if post_result.passed and not actions_for_retry:
                            break
                        if not actions_for_retry:
                            if debug:
                                console.print_debug(
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
                            if post_warn_on_failure:
                                console.print_warning(
                                    "Post-review could not obtain a visible final reply; fail-closed."
                                )
                            fail_closed = True
                            break
                        if post_allow_unresolved:
                            console.print_warning(
                                "Post-review detected repeated unresolved actions; "
                                "allow_unresolved=true, sending reply with warning."
                            )
                            break
                        if post_warn_on_failure:
                            console.print_warning(
                                "Post-review detected repeated unresolved actions; fail-closed."
                            )
                        if debug:
                            console.print_debug(
                                "post-review",
                                "same retry signature repeated, fail-closed",
                            )
                        fail_closed = True
                        break
                    last_action_signature = signature

                    if retry_count >= post_max_retries:
                        if need_visible_reply_retry:
                            if post_warn_on_failure:
                                console.print_warning(
                                    "Post-review could not obtain a visible final reply after max retries; fail-closed."
                                )
                            fail_closed = True
                            break
                        if post_allow_unresolved:
                            console.print_warning(
                                "Post-review found unresolved actions after max retries; "
                                "allow_unresolved=true, sending reply with warning."
                            )
                            break
                        if post_warn_on_failure:
                            console.print_warning(
                                "Post-review found unresolved actions after max retries; fail-closed."
                            )
                        fail_closed = True
                        break

                    retry_count += 1
                    if debug:
                        console.print_debug("post-review", f"retry {retry_count}/{post_max_retries}")

                    # Keep previous tool calls/results in conversation so the
                    # brain sees its prior work (e.g. boot) and doesn't redo it.
                    # Inject as synthetic tool call + result to avoid mutating
                    # system_instruction (which invalidates prompt cache on
                    # OpenRouter/Gemini).
                    if need_visible_reply_retry:
                        directive = _build_missing_visible_reply_directive(
                            retry_instruction=retry_instruction,
                            attempt=retry_count,
                            max_attempts=post_max_retries,
                        )
                    else:
                        directive = _build_retry_directive(
                            required_actions=actions_for_retry,
                            retry_instruction=retry_instruction,
                            attempt=retry_count,
                            max_attempts=post_max_retries,
                        )
                    retry_tool_id = f"retry-{uuid.uuid4().hex[:8]}"
                    conversation.add_assistant_with_tools(
                        None,
                        [ToolCall(id=retry_tool_id, name="_post_review", arguments={})],
                    )
                    conversation.add_tool_result(retry_tool_id, "_post_review", directive)
                    review_attempt_anchor = len(conversation.get_messages())
                    messages = builder.build(conversation)
                    response = _run_responder(
                        client, messages, tools,
                        conversation, builder, registry, console,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        memory_edit_allow_failure=memory_edit_allow_failure,
                        progress_reviewer=progress_reviewer,
                        progress_review_warn_on_failure=progress_warn_on_failure,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        conversation.get_messages()[turn_anchor:],
                    )
                if fail_closed:
                    conversation._messages = conversation._messages[:turn_anchor]
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot,
                        console=console,
                        debug=debug,
                    )
                    console.print_error(
                        "Post-review unresolved (fail-closed); no assistant reply was sent."
                    )
                    continue
                if final_content and not used_fallback_content:
                    conversation.add("assistant", final_content)
                console.print_assistant(final_content)
            else:
                if final_content and not used_fallback_content:
                    conversation.add("assistant", final_content)
                elif not final_content:
                    # Persist intermediate text for resume when no final reply.
                    turn_msgs = conversation.get_messages()[turn_anchor:]
                    intermediate = _latest_intermediate_text(turn_msgs)
                    if intermediate:
                        conversation.add("assistant", intermediate)
                console.print_assistant(final_content)

            # Post-turn hooks
            _run_memory_archive(agent_os_dir, config, console)
            _run_memory_backup(memory_backup_mgr)

        except ContextLengthExceededError:
            _rollback_turn_memory_changes(
                turn_memory_snapshot, console=console, debug=debug,
            )
            conversation._messages = conversation._messages[:pre_turn_anchor]

            # Archive before retry to shrink boot files (e.g. short-term.md)
            _run_memory_archive(agent_os_dir, config, console)

            # Retry with progressively fewer turns:
            # Always reduce preserve_turns first to make room for tool results,
            # avoiding the LLM re-executing the same tool calls that caused overflow.
            _min_preserve = 2
            while True:
                if builder.preserve_turns <= _min_preserve:
                    console.print_error(
                        "Context still too large after reducing to minimum turns."
                    )
                    break
                builder.preserve_turns = max(
                    _min_preserve, builder.preserve_turns // 2,
                )
                console.print_warning(
                    f"Token limit exceeded. "
                    f"Reducing preserve_turns to {builder.preserve_turns}, retrying..."
                )

                conversation.add("user", user_input)
                messages = builder.build(conversation)
                turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=agent_os_dir)
                try:
                    tools = registry.get_definitions()
                    turn_anchor = len(conversation.get_messages())
                    response = _run_responder(
                        client, messages, tools,
                        conversation, builder, registry, console,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        memory_edit_allow_failure=memory_edit_allow_failure,
                        progress_reviewer=progress_reviewer,
                        progress_review_warn_on_failure=progress_warn_on_failure,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        conversation.get_messages()[turn_anchor:],
                    )
                    if final_content and not used_fallback_content:
                        conversation.add("assistant", final_content)
                    console.print_assistant(final_content)
                    _run_memory_archive(agent_os_dir, config, console)
                    _run_memory_backup(memory_backup_mgr)
                    break
                except ContextLengthExceededError:
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot, console=console, debug=debug,
                    )
                    conversation._messages = conversation._messages[:pre_turn_anchor]
                    continue
                except Exception as e:
                    _rollback_turn_memory_changes(
                        turn_memory_snapshot, console=console, debug=debug,
                    )
                    console.print_error(_sanitize_error_message(str(e)))
                    conversation._messages = conversation._messages[:pre_turn_anchor]
                    break

        except KeyboardInterrupt:
            # Preserve completed work; patch incomplete tool calls for API consistency
            _patch_interrupted_tool_calls(conversation, turn_anchor)
            session_mgr.rewrite_messages(conversation.get_messages())
            console.print_info("Interrupted.")
            continue

        except Exception as e:
            _rollback_turn_memory_changes(
                turn_memory_snapshot,
                console=console,
                debug=debug,
            )
            console.print_error(_sanitize_error_message(str(e)))
            conversation._messages = conversation._messages[:pre_turn_anchor]
            continue

        finally:
            esc_monitor.stop()
