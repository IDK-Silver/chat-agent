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
    RequiredAction,
    ReviewPacketConfig,
    build_post_review_packet,
)
from ..reviewer.enforcement import (
    collect_turn_tool_calls,
    detect_persistence_anomalies,
    extract_memory_edit_paths,
    is_failed_memory_edit_result,
    find_missing_actions,
    build_target_enforcement_actions,
    merge_anomaly_signals,
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


def _is_memory_path(path: str, *, working_dir: Path) -> bool:
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
        candidate = working_dir / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to((working_dir / "memory").resolve())
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

    def __init__(self, *, working_dir: Path):
        self._working_dir = working_dir
        self._memory_root = (working_dir / "memory").resolve()
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
            candidate = self._working_dir / candidate

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._memory_root)
        except ValueError:
            return None
        return resolved


def _build_memory_shell_write_patterns(working_dir: Path) -> list[re.Pattern[str]]:
    """Build shell patterns that indicate direct memory writes."""
    memory_abs = re.escape(str((working_dir / "memory").resolve()))
    memory_rel = r"(?:\./)?(?:\.agent/)?memory/"
    memory_target = rf"(?:['\"])?(?:{memory_rel}|{memory_abs}/)"
    return [
        re.compile(rf">>?\s*{memory_target}"),
        re.compile(rf"\btee(?:\s+-a)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\bsed\s+-i(?:\S*)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\brm\s[^\n]*{memory_target}"),
        re.compile(rf"\bmv\s[^\n]*{memory_target}"),
    ]


def _is_memory_write_shell_command(command: str, *, working_dir: Path) -> bool:
    """Check if command contains shell patterns that write under memory/."""
    return any(
        pattern.search(command) is not None
        for pattern in _build_memory_shell_write_patterns(working_dir)
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


def _build_retry_directive(
    required_actions: list[RequiredAction],
    violations: list[str] | None = None,
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

    if violations:
        parts.append("")
        parts.append("Fix violations: " + ", ".join(violations))

    if retry_instruction:
        parts.append("")
        parts.append(retry_instruction)

    if required_actions:
        parts.extend([
            "",
            "completion_criteria:",
            "- Every required action above is completed successfully in this attempt.",
            "- All missing_targets listed above are written successfully.",
            "- No unresolved anomaly signal remains.",
            "",
            "hard_rule:",
            "- Do NOT output user-facing reply before completion.",
            "- If completion_criteria is not met, continue tool calls now.",
        ])
        parts.append("")
        parts.append("Execute now.")
    else:
        parts.append("")
        parts.append("Fix and re-answer.")

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
    working_dir: Path,
    *,
    memory_editor: MemoryEditor | None = None,
    memory_search_agent: MemorySearchAgent | None = None,
) -> ToolRegistry:
    """Set up the tool registry with built-in tools.

    Args:
        tools_config: Tools configuration
        working_dir: Application working directory (for file access)
    """
    registry = ToolRegistry()

    # Time tool
    registry.register("get_current_time", get_current_time, GET_CURRENT_TIME_DEFINITION)

    # Shell executor - use working_dir
    executor = ShellExecutor(
        working_dir=working_dir,
        blacklist=tools_config.shell.blacklist,
        timeout=tools_config.shell.timeout,
    )
    base_execute_shell = create_execute_shell(executor)

    def guarded_execute_shell(command: str, timeout: int | None = None) -> str:
        if _is_memory_write_shell_command(command, working_dir=working_dir):
            return "Error: Direct memory writes via shell are blocked. Use memory_edit."
        return base_execute_shell(command, timeout)

    registry.register("execute_shell", guarded_execute_shell, EXECUTE_SHELL_DEFINITION)

    # File tools - allow access to working_dir
    allowed_paths = list(tools_config.allowed_paths)
    # Always allow working_dir for memory access
    allowed_paths.insert(0, str(working_dir))

    registry.register(
        "read_file",
        create_read_file(allowed_paths, working_dir),
        READ_FILE_DEFINITION,
    )
    base_write_file = create_write_file(allowed_paths, working_dir)
    base_edit_file = create_edit_file(allowed_paths, working_dir)

    def guarded_write_file(path: str, content: str) -> str:
        if _is_memory_path(path, working_dir=working_dir):
            return "Error: Direct memory writes are blocked. Use memory_edit."
        return base_write_file(path, content)

    def guarded_edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        if _is_memory_path(path, working_dir=working_dir):
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
                base_dir=working_dir,
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

    return registry


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
) -> LLMResponse:
    """Run responder with tool call loop. Returns final response."""
    with console.spinner():
        response = client.chat_with_tools(messages, tools)
    _debug_print_responder_output(console, response, label="responder")

    memory_edit_fail_streak = 0
    while response.has_tool_calls():
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
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)
            if tool_call.name == "memory_edit" and is_failed_memory_edit_result(result):
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


def _run_memory_archive(working_dir: Path, config: AppConfig, console: ChatConsole):
    """Run memory archive hook; log and swallow errors."""
    try:
        result = check_and_archive_buffers(working_dir, config.hooks.memory_archive)
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
    working_dir: Path | None = None,
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
    if working_dir and config:
        _run_memory_archive(working_dir, config, console)
    _run_memory_backup(memory_backup_mgr)
    console.print_goodbye()


def main(user: str, resume: str | None = None) -> None:
    """Main entry point for the CLI."""
    user_selector = user.strip()
    if not user_selector:
        raise ValueError("user is required")

    config = load_config()
    working_dir = config.get_working_dir()

    # Check workspace initialization
    workspace = WorkspaceManager(working_dir)
    console = ChatConsole()

    if not workspace.is_initialized():
        console.print_error(f"Workspace not initialized at {working_dir}")
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

    # Load bootloader prompt
    try:
        system_prompt = workspace.get_system_prompt("brain")
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
    session_mgr = SessionManager(working_dir / "sessions")
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
        working_dir=working_dir,
        boot_files=config.context.boot_files,
        max_chars=config.context.max_chars,
        preserve_turns=config.context.preserve_turns,
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
                memory_dir=working_dir / "memory",
                parse_retries=ms_config.pre_parse_retries,
                parse_retry_prompt=ms_parse_retry,
                context_bytes_limit=ms_config.context_bytes_limit,
                max_results=ms_config.max_results,
            )
        except FileNotFoundError:
            pass

    registry = setup_tools(
        config.tools,
        working_dir,
        memory_editor=memory_editor,
        memory_search_agent=memory_search_agent,
    )
    memory_edit_allow_failure = config.tools.memory_edit.allow_failure
    commands = CommandHandler(console)

    post_reviewer = None
    post_max_retries = 2
    post_allow_unresolved = False
    post_warn_on_failure = True
    post_review_packet_config = ReviewPacketConfig()
    if "post_reviewer" in config.agents and config.agents["post_reviewer"].enabled:
        post_config = config.agents["post_reviewer"]
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
        except FileNotFoundError:
            pass

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
        memory_backup_mgr = MemoryBackupManager(working_dir, config.hooks.memory_backup)

    if resume is None:
        console.print_welcome()

    while True:
        user_input = chat_input.get_input()

        if user_input is None:
            _graceful_exit(
                client, conversation, builder, registry,
                console, workspace, user_id,
                working_dir=working_dir,
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
                    working_dir=working_dir,
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
            elif result == CommandResult.RELOAD_SYSTEM_PROMPT:
                try:
                    builder.system_prompt = workspace.get_system_prompt("brain")
                    console.print_info("System prompt reloaded.")
                except FileNotFoundError as e:
                    console.print_error(f"Failed to reload system prompt: {e}")
            continue

        pre_turn_anchor = len(conversation.get_messages())
        conversation.add("user", user_input)
        has_new_user_content = True
        messages = builder.build(conversation)
        turn_memory_snapshot = _TurnMemorySnapshot(working_dir=working_dir)

        esc_monitor = EscInterruptMonitor()
        try:
            esc_monitor.start()
            tools = registry.get_definitions()

            # === Responder ===
            turn_anchor = len(conversation.get_messages())
            response = _run_responder(
                client, messages, tools,
                conversation, builder, registry, console,
                on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                memory_edit_allow_failure=memory_edit_allow_failure,
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
                sticky_target_signals: dict[str, TargetSignal] = {}
                while True:
                    # Early detection: skip post-review LLM call for empty responses
                    if not final_content or not final_content.strip():
                        if debug:
                            console.print_debug(
                                "post-review", "empty response detected, skipping review",
                            )
                        post_result = PostReviewResult(
                            passed=False,
                            violations=["empty_reply"],
                            retry_instruction="回覆為空，請提供有意義的回應。",
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

                    # Strict target-signal enforcement and anomaly checks.
                    turn_messages = conversation.get_messages()[turn_anchor:]
                    attempt_messages = conversation.get_messages()[review_attempt_anchor:]
                    violations = _filter_retry_violations(
                        post_result.violations,
                        turn_messages=turn_messages,
                    )
                    effective_target_signals = _resolve_effective_target_signals(
                        post_result.target_signals,
                        sticky_target_signals,
                    )
                    target_enforcement_actions = build_target_enforcement_actions(
                        effective_target_signals,
                        turn_messages,
                        current_user=user_id,
                    )
                    deterministic_anomaly_signals = detect_persistence_anomalies(
                        effective_target_signals,
                        turn_messages,
                        current_user=user_id,
                        attempt_messages=attempt_messages,
                    )
                    merged_anomaly_signals = merge_anomaly_signals(
                        post_result.anomaly_signals,
                        deterministic_anomaly_signals,
                    )
                    _promote_anomaly_targets_to_sticky(
                        sticky_target_signals,
                        merged_anomaly_signals,
                    )

                    # Determine final passed state before debug display.
                    override_reason = ""
                    if post_result.passed:
                        if (
                            post_result.required_actions
                            or post_result.violations
                            or target_enforcement_actions
                            or merged_anomaly_signals
                        ):
                            override_reason = (
                                "contradictory output: passed=true with "
                                f"actions={len(post_result.required_actions)} "
                                f"violations={len(post_result.violations)}, "
                                "treating as failed"
                            )
                            post_result.passed = False

                    if debug:
                        raw = post_reviewer.last_raw_response or "(empty)"
                        console.print_debug_block(
                            "post-review raw", _format_debug_json(raw),
                        )
                        if override_reason:
                            console.print_debug("post-review", override_reason)
                        for v in violations:
                            console.print_debug("post-review violation", v)
                        for action in post_result.required_actions:
                            console.print_debug(
                                "post-review action",
                                f"{action.code} | tool={action.tool} | "
                                f"path={action.target_path or action.target_path_glob or '-'}",
                            )
                        if post_result.retry_instruction:
                            console.print_debug(
                                "post-review instruction",
                                post_result.retry_instruction,
                            )
                        elif post_result.guidance:
                            console.print_debug(
                                "post-review guidance",
                                post_result.guidance,
                            )
                        target_lines = [
                            f"- {signal.signal}:{'persist' if signal.requires_persistence else 'skip'}"
                            for signal in effective_target_signals
                        ]
                        if target_lines:
                            console.print_debug_block(
                                "post-review targets",
                                "\n".join(target_lines),
                            )
                        else:
                            console.print_debug("post-review targets", "(none)")

                        anomaly_lines = [
                            f"- {signal.signal}:{signal.target_signal or '-'}"
                            for signal in merged_anomaly_signals
                        ]
                        if anomaly_lines:
                            console.print_debug_block(
                                "post-review anomalies",
                                "\n".join(anomaly_lines),
                            )
                        else:
                            console.print_debug("post-review anomalies", "(none)")
                        if post_result.passed:
                            console.print_debug("post-review", "PASS")
                        elif target_enforcement_actions or merged_anomaly_signals:
                            codes = ", ".join(
                                a.code for a in target_enforcement_actions
                            )
                            anomaly_count = len(merged_anomaly_signals)
                            console.print_debug(
                                "post-review",
                                "FAIL (target/anomaly enforcement: "
                                f"{codes or '-'} | anomalies={anomaly_count})",
                            )
                        else:
                            console.print_debug("post-review", "FAIL")

                    if post_result.passed:
                        break

                    turn_missing_memory_write = not _has_memory_write(turn_messages)
                    retry_instruction = (
                        post_result.retry_instruction
                        or (post_result.guidance or "")
                    )
                    actions_for_retry = _collect_required_actions_for_retry(
                        turn_messages,
                        passed=False,
                        required_actions=post_result.required_actions,
                    )
                    missing_actions = find_missing_actions(
                        turn_messages,
                        post_result.required_actions,
                    )

                    if post_result.required_actions and not missing_actions:
                        if debug:
                            console.print_debug(
                                "post-review",
                                "required actions already satisfied in this attempt; accepting",
                            )

                    # Merge deterministic target enforcement actions.
                    if target_enforcement_actions:
                        existing_codes = {a.code for a in actions_for_retry}
                        for action in target_enforcement_actions:
                            if action.code not in existing_codes:
                                actions_for_retry.append(action)
                        if not retry_instruction:
                            retry_instruction = (
                                "Complete required memory writes before final answer."
                            )

                    if merged_anomaly_signals:
                        anomaly_violations = [
                            f"{a.signal}:{a.target_signal or '-'}"
                            for a in merged_anomaly_signals
                        ]
                        violations = list(dict.fromkeys([*violations, *anomaly_violations]))
                        anomaly_instruction = _format_anomaly_retry_instruction(
                            merged_anomaly_signals
                        )
                        retry_instruction = "\n\n".join(
                            part for part in [retry_instruction, anomaly_instruction] if part
                        )

                    if turn_missing_memory_write:
                        actions_for_retry = _ensure_turn_persistence_action(actions_for_retry)
                        if not retry_instruction:
                            retry_instruction = (
                                "Persist this turn to memory before final answer."
                            )

                    if not actions_for_retry and not violations and not merged_anomaly_signals:
                        break

                    signature = _action_signature(
                        actions_for_retry,
                        violations,
                        merged_anomaly_signals,
                    )
                    if signature and signature == last_action_signature:
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
                    directive = _build_retry_directive(
                        required_actions=actions_for_retry,
                        violations=violations,
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
                console.print_assistant(final_content)

            # Post-turn hooks
            _run_memory_archive(working_dir, config, console)
            _run_memory_backup(memory_backup_mgr)

        except ContextLengthExceededError:
            _rollback_turn_memory_changes(
                turn_memory_snapshot, console=console, debug=debug,
            )
            conversation._messages = conversation._messages[:pre_turn_anchor]

            # Retry with progressively fewer turns:
            # 1st attempt: rebuild with existing truncation settings
            # subsequent: halve preserve_turns until success or min reached
            _min_preserve = 2
            _first_retry = True
            while True:
                if _first_retry:
                    console.print_warning(
                        "Token limit exceeded. Rebuilding context and retrying..."
                    )
                    _first_retry = False
                else:
                    if builder.preserve_turns <= _min_preserve:
                        console.print_error(
                            "Context still too large after reducing to minimum turns."
                        )
                        break
                    builder.preserve_turns = max(
                        _min_preserve, builder.preserve_turns // 2,
                    )
                    console.print_warning(
                        f"Still exceeds limit. "
                        f"Reducing preserve_turns to {builder.preserve_turns}..."
                    )

                conversation.add("user", user_input)
                messages = builder.build(conversation)
                turn_memory_snapshot = _TurnMemorySnapshot(working_dir=working_dir)
                try:
                    tools = registry.get_definitions()
                    turn_anchor = len(conversation.get_messages())
                    response = _run_responder(
                        client, messages, tools,
                        conversation, builder, registry, console,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        memory_edit_allow_failure=memory_edit_allow_failure,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        conversation.get_messages()[turn_anchor:],
                    )
                    if final_content and not used_fallback_content:
                        conversation.add("assistant", final_content)
                    console.print_assistant(final_content)
                    _run_memory_archive(working_dir, config, console)
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
            _rollback_turn_memory_changes(
                turn_memory_snapshot,
                console=console,
                debug=debug,
            )
            conversation._messages = conversation._messages[:pre_turn_anchor]
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
