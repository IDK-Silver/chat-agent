"""Agent core logic: responder + memory sync.

Extracted from cli/app.py to decouple agent logic from CLI adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.protocol import ChannelAdapter
    from .contact_map import ContactMap

from ..cli.console import ChatConsole
from ..cli.interrupt import EscInterruptMonitor
from ..context import ContextBuilder, Conversation
from ..core.schema import AppConfig, ContextRefreshConfig, ToolsConfig
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
from ..session.schema import SessionEntry
from ..tools import (
    ToolRegistry,
    ShellExecutor,
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
from .schema import InboundMessage, RefreshSentinel, ShutdownSentinel
from .turn_context import TurnContext

_TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]]*\]\s*")
_MEMORY_EDIT_RETRY_LIMIT = 3
_MAX_RESPONDER_ITERATIONS = 10
_DEBUG_RESPONSE_PREVIEW_CHARS = 4000
_SENSITIVE_URL_PARAM_RE = re.compile(r"([?&](?:key|api_key|token|access_token)=)[^&\s]+", re.IGNORECASE)
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
logger = logging.getLogger(__name__)


def _strip_timestamp_prefix(text: str) -> str:
    """Strip leading [YYYY-MM-DD HH:MM...] prefix that LLM may echo."""
    return _TIMESTAMP_PREFIX_RE.sub("", text)


def _latest_nonempty_assistant_content(messages: list[SessionEntry]) -> str:
    """Return the newest non-empty assistant content from non-tool messages."""
    for msg in reversed(messages):
        if msg.role != "assistant" or msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or ""
    return ""


def _latest_intermediate_text(messages: list[SessionEntry]) -> str:
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
    turn_messages: list[SessionEntry],
) -> tuple[str, bool]:
    """Resolve user-visible content; fallback to prior assistant text.

    Returns (content, is_fallback).  ``is_fallback=True`` means the content
    was already emitted during the tool-call loop via ``print_assistant``.
    """
    if isinstance(response_content, str) and response_content.strip():
        return response_content, False

    fallback = _latest_nonempty_assistant_content(turn_messages)
    if fallback:
        return fallback, True

    # Text produced alongside tool_calls (already shown in processing section)
    intermediate = _latest_intermediate_text(turn_messages)
    if intermediate:
        return intermediate, True

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
    finish = response.finish_reason or "?"
    console.print_debug(
        label,
        f"content_chars={len(content)}, tool_calls={len(tool_calls)}, "
        f"finish={finish}, tools=[{tool_names}]",
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
    contact_map: ContactMap | None = None,
    extra_allowed_paths: list[str] | None = None,
) -> tuple[ToolRegistry, list[str]]:
    """Set up the tool registry with built-in tools.

    Args:
        tools_config: Tools configuration
        agent_os_dir: Application working directory (for file access)

    Returns:
        (registry, allowed_paths) -- the resolved allowed paths list
        can be reused by callers (e.g. send_message tool).
    """
    registry = ToolRegistry()

    # Shell executor - use agent_os_dir
    executor = ShellExecutor(
        agent_os_dir=agent_os_dir,
        blacklist=tools_config.shell.blacklist,
        timeout=tools_config.shell.timeout,
        export_env=tools_config.shell.export_env,
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
    # Additional paths (e.g. Gmail attachment temp dir)
    if extra_allowed_paths:
        allowed_paths.extend(extra_allowed_paths)

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
            create_gui_task(
                gui_manager, gui_lock=gui_lock,
                agent_os_dir=agent_os_dir,
            ),
            GUI_TASK_DEFINITION,
        )

    # Contact mapping tool (sender identity cache)
    if contact_map is not None:
        from ..tools.builtin.contact_mapping import (
            UPDATE_CONTACT_MAPPING_DEFINITION,
            create_update_contact_mapping,
        )
        registry.register(
            "update_contact_mapping",
            create_update_contact_mapping(contact_map),
            UPDATE_CONTACT_MAPPING_DEFINITION,
        )

    return registry, allowed_paths


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
    iterations = 0
    while response.has_tool_calls():
        iterations += 1
        if iterations > _MAX_RESPONDER_ITERATIONS:
            logger.warning(
                "Responder loop exceeded %d iterations; breaking.",
                _MAX_RESPONDER_ITERATIONS,
            )
            console.print_warning(
                f"Tool loop exceeded {_MAX_RESPONDER_ITERATIONS} iterations; stopping.",
            )
            break
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
    "[SYSTEM] Your previous response was empty. "
    "As a companion, you must always reply to the user. "
    "Respond naturally to their message now. "
    "Do not call any tools. Just talk."
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


class _RefreshTimer:
    """Background timer that enqueues RefreshSentinel when refresh is due."""

    def __init__(
        self,
        queue: PersistentPriorityQueue,
        config: ContextRefreshConfig,
    ):
        self._queue = queue
        self._interval = timedelta(hours=config.interval_hours)
        self._on_day_change = config.on_day_change
        self._last_refresh = datetime.now()
        self._last_date = datetime.now().date()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def mark_refreshed(self) -> None:
        """Called after successful refresh to reset timers."""
        self._last_refresh = datetime.now()
        self._last_date = datetime.now().date()

    def _loop_once(self) -> bool:
        """Check conditions and enqueue sentinel if due. Returns True if enqueued."""
        now = datetime.now()
        day_changed = self._on_day_change and now.date() != self._last_date
        interval_elapsed = (now - self._last_refresh) >= self._interval
        if day_changed or interval_elapsed:
            self._queue.put(RefreshSentinel())
            return True
        return False

    def _loop(self) -> None:
        while not self._stop.wait(timeout=60):
            if self._loop_once():
                # Avoid spamming; wait 5min before next check
                self._stop.wait(timeout=300)


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
        # Turn context for send_message tool
        turn_context: TurnContext | None = None,
        # Context refresh
        context_refresh_config: ContextRefreshConfig | None = None,
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
        self.turn_context = turn_context
        self._context_refresh_config = context_refresh_config
        self._refresh_timer: _RefreshTimer | None = None
        self.adapters: dict[str, ChannelAdapter] = {}

    def run_turn(
        self,
        user_input: str,
        *,
        output_fn: Callable[[str | None], None] | None = None,
        channel: str = "cli",
        sender: str | None = None,
        timestamp: datetime | None = None,
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
                self.console.print_inner_thoughts(channel, sender, content)
        debug = self.console.debug
        pre_turn_anchor = len(self.conversation.get_messages())
        self.conversation.add("user", user_input, channel=channel, sender=sender, timestamp=timestamp)
        messages = self.builder.build(self.conversation)

        if debug:
            # Show the last user message as seen by LLM (with timestamp prefix)
            for m in reversed(messages):
                if m.role == "user" and isinstance(m.content, str):
                    self.console.print_debug("context", m.content[:200])
                    break

        # Start new session if context was truncated
        if self.builder.last_was_truncated:
            self.session_mgr.finalize("truncated")
            self.session_mgr.create(self.user_id, self.display_name)
            self.conversation._on_message = self.session_mgr.append_message

        turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
        turn_anchor = len(self.conversation.get_messages())

        esc_monitor = EscInterruptMonitor()
        try:
            if channel == "cli":
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
            final_content = _strip_timestamp_prefix(final_content)
            if debug:
                self.console.print_debug(
                    "resolve",
                    f"final_content_chars={len(final_content)}, "
                    f"used_fallback={used_fallback_content}",
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
                    if debug:
                        self.console.print_debug("memory-sync", "done")
                except ContextLengthExceededError:
                    if debug:
                        self.console.print_debug(
                            "memory-sync", "skipped: context length exceeded",
                        )
                except Exception:
                    if debug:
                        self.console.print_debug("memory-sync", "side-channel failed")
            elif debug:
                self.console.print_debug("memory-sync", "no missing targets")

            # === Finalize: thoughts first, then responses ===
            # Text output is inner thoughts (console only); actual delivery
            # happens via send_message tool calls during the responder loop.
            if final_content and not used_fallback_content:
                self.conversation.add("assistant", final_content)
            _output(final_content or None)

            # Flush buffered outbound messages (deferred from send_message)
            if self.turn_context is not None:
                for msg in self.turn_context.pending_outbound:
                    self.console.print_outbound(
                        msg.channel, msg.recipient, msg.body,
                        attachments=msg.attachments or None,
                    )
                self.turn_context.pending_outbound.clear()

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

                self.conversation.add("user", user_input, channel=channel, sender=sender)
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
                    final_content = _strip_timestamp_prefix(final_content)
                    # Empty response fallback (retry path)
                    if final_content and not used_fallback_content:
                        self.conversation.add("assistant", final_content)
                    _output(final_content or None)
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

    def _perform_context_refresh(self) -> None:
        """Compact conversation, reload boot files, rotate session."""
        cfg = self._context_refresh_config
        if cfg is None:
            return

        try:
            # 1. Compact conversation
            removed = self.conversation.compact(cfg.preserve_turns)

            # 2. Re-resolve system prompt with current date
            try:
                raw_prompt = self.workspace.get_system_prompt("brain")
                raw_prompt = raw_prompt.replace(
                    "{agent_os_dir}", str(self.agent_os_dir),
                )
                self.builder.update_system_prompt(raw_prompt)
            except FileNotFoundError:
                logger.warning("Context refresh: failed to reload system prompt")

            # 3. Reload boot files from disk
            self.builder.reload_boot_files()

            # 4. Session rotation
            if self.session_mgr is not None:
                self.session_mgr.finalize("refreshed")
                self.session_mgr.create(self.user_id, self.display_name)
                self.conversation._on_message = self.session_mgr.append_message
                # Persist kept messages to new session
                for entry in self.conversation.get_messages():
                    self.session_mgr.append_message(entry)

            # 5. Mark timer
            if self._refresh_timer:
                self._refresh_timer.mark_refreshed()

            self.console.print_info(
                f"Context refreshed: {removed} messages compacted, "
                f"boot files reloaded, new session started."
            )
        except Exception as e:
            logger.warning("Context refresh failed: %s", e)

    def _schedule_next_heartbeat(self, msg: InboundMessage) -> None:
        """Create the next recurring heartbeat after a successful turn."""
        from .adapters.scheduler import make_heartbeat_message, random_delay

        recur_spec = msg.metadata.get("recur_spec", "2h-5h")
        try:
            delay = random_delay(recur_spec)
        except ValueError:
            logger.warning("Invalid recur_spec %r; using default 2h-5h", recur_spec)
            delay = random_delay("2h-5h")

        next_time = datetime.now(timezone.utc) + delay
        next_msg = make_heartbeat_message(
            not_before=next_time,
            interval_spec=recur_spec,
        )
        self._queue.put(next_msg)
        logger.info(
            "Next heartbeat in %.1f hours",
            delay.total_seconds() / 3600,
        )

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

        # Start context refresh timer if configured
        if self._context_refresh_config and self._context_refresh_config.enabled:
            self._refresh_timer = _RefreshTimer(
                self._queue, self._context_refresh_config,
            )
            self._refresh_timer.start()

        # Start delayed message promotion thread
        self._queue.start_promotion()

        try:
            while True:
                msg, receipt = self._queue.get()
                if isinstance(msg, ShutdownSentinel):
                    if msg.graceful:
                        self.graceful_exit()
                    break
                if isinstance(msg, RefreshSentinel):
                    if self._queue.pending_count() == 0:
                        self._perform_context_refresh()
                    continue
                self._process_inbound(msg, receipt)
        except KeyboardInterrupt:
            self.graceful_exit()
        finally:
            self._queue.stop_promotion()
            if self._refresh_timer:
                self._refresh_timer.stop()
            for adapter in self.adapters.values():
                adapter.stop()

    def _process_inbound(self, msg: InboundMessage, receipt: Path | None) -> None:
        """Process one inbound message through the turn pipeline."""
        # Notify all adapters so terminal-owning ones (CLI) can suspend
        for a in self.adapters.values():
            a.on_turn_start(msg.channel)

        self.console.print_inbound(msg.channel, msg.sender, msg.content)
        self.console.print_processing(msg.channel, msg.sender)

        # Update turn context so send_message tool knows current inbound info
        if self.turn_context is not None:
            self.turn_context.set_inbound(msg.channel, msg.sender, msg.metadata)

        # Inner thoughts callback: display on console only, never sent.
        # Actual message delivery happens via the send_message tool.
        def _thoughts(content: str | None) -> None:
            self.console.print_inner_thoughts(msg.channel, msg.sender, content)

        completed = False
        try:
            self.run_turn(
                msg.content, output_fn=_thoughts,
                channel=msg.channel, sender=msg.sender,
                timestamp=msg.timestamp,
            )
            completed = True
        finally:
            if self.turn_context is not None:
                self.turn_context.clear()
            if self._queue is not None and completed:
                self._queue.ack(receipt)
                # Auto-schedule next heartbeat for recurring messages
                if msg.metadata.get("recurring"):
                    self._schedule_next_heartbeat(msg)
            for a in self.adapters.values():
                a.on_turn_complete()

