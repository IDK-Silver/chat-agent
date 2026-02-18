"""Agent core logic: responder + memory sync.

Extracted from cli/app.py to decouple agent logic from CLI adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.protocol import ChannelAdapter

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
    find_missing_memory_sync_targets,
    extract_memory_edit_paths,
    is_failed_memory_edit_result,
)
from ..memory.backup import MemoryBackupManager
from ..memory.hooks import check_and_archive_buffers
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
from .queue import PersistentPriorityQueue
from .schema import InboundMessage, OutboundMessage, ShutdownSentinel

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



def _build_memory_sync_reminder(missing_targets: list[str]) -> str:
    """Build directive for the memory-sync side-channel LLM call."""
    targets = "\n".join(f"- {t}" for t in missing_targets)
    return (
        "[MEMORY SYNC]\n"
        f"You have not updated the following files this turn:\n{targets}\n"
        "Call memory_edit to update them now."
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
    gui_lock: threading.Lock | None = None,
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
            create_gui_task(gui_manager, gui_lock=gui_lock),
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


_EMPTY_RESPONSE_NUDGE = (
    "[SYSTEM] You executed tools but did not reply to the user. "
    "Please respond in natural language now."
)


def _run_empty_response_fallback(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    console: ChatConsole,
) -> str:
    """Side-channel LLM call to get a text response when responder returned empty.

    Builds a local copy of the conversation, appends a nudge prompt,
    and calls the LLM without tools to force a text reply.
    """
    local_messages = builder.build(conversation)
    local_messages.append(
        Message(role="user", content=_EMPTY_RESPONSE_NUDGE),
    )
    with console.spinner():
        response = client.chat(local_messages)
    if response and response.strip():
        return response
    return ""


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
    """Core agent logic: responder + memory sync."""

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
        # Memory
        memory_edit_allow_failure: bool = False,
        memory_backup_mgr: MemoryBackupManager | None = None,
        # Queue
        queue: PersistentPriorityQueue | None = None,
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
        self.memory_edit_allow_failure = memory_edit_allow_failure
        self.memory_backup_mgr = memory_backup_mgr
        self._queue = queue
        self.adapters: dict[str, ChannelAdapter] = {}

    def run_turn(
        self,
        user_input: str,
        *,
        output_fn: Callable[[str | None], None] | None = None,
        channel: str = "cli",
        sender: str | None = None,
    ) -> None:
        """Process one user turn.

        Full lifecycle:
        1. Add user message to conversation
        2. Responder (LLM + tool loop)
        3. Memory sync side-channel
        4. Memory archive + backup hooks

        Handles ContextLengthExceededError (reduce preserve_turns + retry),
        KeyboardInterrupt (patch incomplete tool calls), and general exceptions
        (rollback memory + restore conversation).

        Args:
            output_fn: Callback for the final response.  When *None* the
                direct-call path is used with channel display sections.
            channel: Channel name for display (direct-call path only).
            sender: Sender name for display (direct-call path only).
        """
        if output_fn is not None:
            _output = output_fn
        else:
            # Direct-call path: show channel display sections
            self.console.print_inbound(channel, sender, user_input)
            self.console.print_processing(channel, sender)

            def _output(content: str | None) -> None:
                self.console.print_outbound(channel, sender, content)
        debug = self.console.debug
        pre_turn_anchor = len(self.conversation.get_messages())
        self.conversation.add("user", user_input)
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

            # === Empty response fallback ===
            if not final_content.strip() and not used_fallback_content:
                if debug:
                    self.console.print_debug(
                        "empty-response", "nudging LLM for text reply",
                    )
                try:
                    final_content = _run_empty_response_fallback(
                        self.client, self.conversation,
                        self.builder, self.console,
                    )
                except Exception:
                    if debug:
                        self.console.print_debug(
                            "empty-response", "fallback failed",
                        )

            # === Finalize response ===
            if final_content and not used_fallback_content:
                self.conversation.add("assistant", final_content)
            elif not final_content:
                turn_msgs = self.conversation.get_messages()[turn_anchor:]
                intermediate = _latest_intermediate_text(turn_msgs)
                if intermediate:
                    self.conversation.add("assistant", intermediate)
            if not used_fallback_content:
                _output(final_content)

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
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        self.conversation.get_messages()[turn_anchor:],
                    )
                    # Empty response fallback (retry path)
                    if not final_content.strip() and not used_fallback_content:
                        try:
                            final_content = _run_empty_response_fallback(
                                self.client, self.conversation,
                                self.builder, self.console,
                            )
                        except Exception:
                            pass
                    if final_content and not used_fallback_content:
                        self.conversation.add("assistant", final_content)
                    if not used_fallback_content:
                        _output(final_content)
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
        """Handle graceful exit."""
        if self.session_mgr is not None:
            self.session_mgr.finalize("completed")

        if self.agent_os_dir and self.config:
            _run_memory_archive(self.agent_os_dir, self.config, self.console)
            if self.config.hooks.session_cleanup.enabled:
                try:
                    from ..session.cleanup import cleanup_sessions
                    cleanup_sessions(
                        self.agent_os_dir / "session",
                        retention_days=self.config.hooks.session_cleanup.retention_days,
                    )
                except Exception as e:
                    logger.warning("Session cleanup failed: %s", e)

        _run_memory_backup(self.memory_backup_mgr)
        self.console.print_goodbye()

    # ------------------------------------------------------------------
    # Queue-based interface
    # ------------------------------------------------------------------

    def register_adapter(self, adapter: ChannelAdapter) -> None:
        """Register a channel adapter."""
        self.adapters[adapter.channel_name] = adapter

    def enqueue(self, msg: InboundMessage | ShutdownSentinel) -> None:
        """Push a message into the persistent queue (thread-safe)."""
        if self._queue is None:
            raise RuntimeError("No queue configured; call AgentCore with queue=...")
        self._queue.put(msg)

    def request_shutdown(self, *, graceful: bool = True) -> None:
        """Signal the agent to shut down via the queue."""
        self.enqueue(ShutdownSentinel(graceful=graceful))

    def run(self) -> None:
        """Queue-based main loop.  Blocks until shutdown.

        Starts all registered adapters, then pulls messages from the
        persistent priority queue.  Each message is processed through
        ``run_turn`` and the response is routed back to the originating
        adapter.
        """
        if self._queue is None:
            raise RuntimeError("No queue configured; call AgentCore with queue=...")

        for adapter in self.adapters.values():
            adapter.start(self)

        try:
            while True:
                msg, receipt = self._queue.get()
                if isinstance(msg, ShutdownSentinel):
                    if msg.graceful:
                        self.graceful_exit()
                    break
                self._process_inbound(msg, receipt)
        except KeyboardInterrupt:
            self.graceful_exit()
        finally:
            for adapter in self.adapters.values():
                adapter.stop()

    def _process_inbound(self, msg: InboundMessage, receipt: Path | None) -> None:
        """Process one inbound message through the turn pipeline."""
        # 1. Display inbound
        self.console.print_inbound(msg.channel, msg.sender, msg.content)
        # 2. Display processing header (tool calls/spinner appear after)
        self.console.print_processing(msg.channel, msg.sender)

        tagged = self._tag_message(msg)
        adapter = self.adapters.get(msg.channel)

        def _route(content: str | None) -> None:
            # 3. Display outbound
            self.console.print_outbound(msg.channel, msg.sender, content)
            if adapter is not None and content:
                adapter.send(OutboundMessage(
                    channel=msg.channel,
                    content=content,
                    metadata=msg.metadata,
                ))

        try:
            self.run_turn(tagged, output_fn=_route)
        finally:
            if self._queue is not None:
                self._queue.ack(receipt)
            if adapter is not None:
                adapter.on_turn_complete()

    def _tag_message(self, msg: InboundMessage) -> str:
        """Add channel tag to message content.

        Only tags when multiple adapters are registered so that
        single-channel usage (Phase 2 CLI-only) stays unchanged.
        """
        if len(self.adapters) <= 1:
            return msg.content
        if msg.sender == self.user_id:
            return f"[{msg.channel}] {msg.content}"
        return f"[{msg.channel}, from {msg.sender}] {msg.content}"
