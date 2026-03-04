"""Agent core logic: responder + memory sync.

Extracted from cli/app.py to decouple agent logic from CLI adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.protocol import ChannelAdapter
    from .contact_map import ContactMap
    from .scope import ScopeResolver
    from .shared_state import SharedStateStore

from ..cli.claude_code_stream_json import (
    extract_text_from_claude_code_stream_json_lines,
)
from ..context import ContextBuilder, Conversation
from ..core.schema import (
    AppConfig,
    ContextRefreshConfig,
    MaintenanceConfig,
    MemoryArchiveConfig,
    ToolsConfig,
)
from ..timezone_utils import parse_timezone_spec
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
    summarize_memory_edit_failure,
)
from ..memory.bm25_search import BM25MemorySearch, create_bm25_memory_search
from ..memory.backup import MemoryBackupManager
from ..memory.hooks import check_and_archive_buffers
from ..session import SessionManager
from ..session.schema import SessionEntry
from ..tui.sink import UiSink
from ..tools import (
    ToolRegistry,
    ShellExecutor,
    EXECUTE_SHELL_DEFINITION,
    create_execute_shell,
    is_claude_code_stream_json_command,
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
    GUIManager,
    GUIWorker,
    SCREENSHOT_BY_SUBAGENT_DEFINITION,
    SCREENSHOT_DEFINITION,
    create_screenshot,
    create_screenshot_by_subagent,
)
from ..workspace import WorkspaceManager
from .queue import PersistentPriorityQueue
from .schema import InboundMessage, MaintenanceSentinel, RefreshSentinel, ShutdownSentinel
from .scope import DEFAULT_SCOPE_RESOLVER
from .staged_planning import (
    STAGE1_SYNTHETIC_TOOL_NAME,
    build_stage1_findings_for_conversation,
    build_stage1_findings_overlay_message,
    build_plan_context_message,
    build_stage3_plan_overlay_message,
    format_stage2_plan_for_tui,
    run_stage1_information_gathering,
    run_stage2_brain_planning,
)
from .turn_effects import analyze_turn_effects
from .turn_context import TurnContext
from .ui_event_console import AgentUiPort, UiEventConsole

_TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]]*\]\s*")
_DEBUG_RESPONSE_PREVIEW_CHARS = 4000
_THINKING_PREVIEW_CHARS = 12000
_SENSITIVE_URL_PARAM_RE = re.compile(r"([?&](?:key|api_key|token|access_token)=)[^&\s]+", re.IGNORECASE)
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
logger = logging.getLogger(__name__)


def _raise_if_cancel_requested(
    is_cancel_requested: Callable[[], bool] | None,
    *,
    on_pending: Callable[[], None] | None = None,
) -> None:
    """Raise KeyboardInterrupt when a turn-level cancel has been requested."""
    if is_cancel_requested is None:
        return
    if not is_cancel_requested():
        return
    if on_pending is not None:
        on_pending()
    raise KeyboardInterrupt


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
    console: AgentUiPort,
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
    reasoning = response.reasoning_content or ""
    finish = response.finish_reason or "?"
    cache_read = response.cache_read_tokens
    cache_write = response.cache_write_tokens
    console.print_debug(
        label,
        f"content_chars={len(content)}, tool_calls={len(tool_calls)}, "
        f"reasoning_chars={len(reasoning)}, finish={finish}, tools=[{tool_names}], "
        f"cache_read={cache_read}, cache_write={cache_write}",
    )
    if cache_read > 0 or cache_write > 0:
        total = cache_read + cache_write
        hit_pct = (cache_read / total * 100) if total > 0 else 0
        logger.info(
            "cache: read=%d write=%d hit=%.0f%%",
            cache_read, cache_write, hit_pct,
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


def _emit_reasoning_block_if_needed(
    console: AgentUiPort,
    response: LLMResponse,
    *,
    channel: str | None,
    sender: str | None,
) -> None:
    """Show tool-loop reasoning in TUI as a side-channel block."""
    if not response.has_tool_calls():
        return
    text = (response.reasoning_content or "").strip()
    if not text:
        return
    total_chars = len(text)
    preview = text
    if len(preview) > _THINKING_PREVIEW_CHARS:
        preview = preview[:_THINKING_PREVIEW_CHARS] + "\n...[truncated]"
    console.print_inner_thoughts(
        channel or "internal",
        sender,
        f"[THINKING][chars={total_chars}]\n{preview}",
    )


def _is_error_tool_result(result: object) -> bool:
    """Return True when a tool result is an error ToolResult."""
    from ..tools.registry import ToolResult

    return isinstance(result, ToolResult) and result.is_error


def _can_short_circuit_terminal_round(
    *,
    tool_calls: list[ToolCall],
    tool_results: dict[str, object],
    tools_config: ToolsConfig | None,
) -> bool:
    """Return True when this tool round can terminate responder immediately."""
    if tools_config is None:
        return False
    cfg = tools_config.terminal_tool_short_circuit
    if not cfg.enabled:
        return False
    if not tool_calls:
        return False

    allowed_tools = set(cfg.allowed_tools)
    allowed_schedule_actions = set(cfg.schedule_action_allowed_actions)
    for tool_call in tool_calls:
        if tool_call.name not in allowed_tools:
            return False
        if tool_call.name == "schedule_action":
            action = tool_call.arguments.get("action")
            if not isinstance(action, str) or action not in allowed_schedule_actions:
                return False
        result = tool_results.get(tool_call.id)
        if result is None:
            return False
        if _is_error_tool_result(result):
            return False
    return True


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


@dataclass
class _TurnTokenUsage:
    """Per-turn usage aggregation for brain responses."""

    usage_available: bool = False
    max_prompt_tokens: int | None = None
    completion_tokens_for_max_prompt: int | None = None
    total_tokens_for_max_prompt: int | None = None
    saw_missing_usage: bool = False

    def record(self, response: LLMResponse) -> None:
        """Track the response with the highest prompt token count."""
        if not response.usage_available:
            self.saw_missing_usage = True
            return
        self.usage_available = True
        if response.prompt_tokens is None:
            return
        if self.max_prompt_tokens is None or response.prompt_tokens >= self.max_prompt_tokens:
            self.max_prompt_tokens = response.prompt_tokens
            self.completion_tokens_for_max_prompt = response.completion_tokens
            self.total_tokens_for_max_prompt = response.total_tokens


@dataclass
class _LatestTokenStatus:
    """Latest token usage shown in the status bar."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_available: bool = False
    missing_usage: bool = False


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



def _build_memory_sync_reminder(
    missing_targets: list[str],
    turns_accumulated: int = 1,
) -> str:
    """Build directive for the memory-sync side-channel LLM call."""
    targets = "\n".join(f"- {t}" for t in missing_targets)
    turn_scope = "1 turn" if turns_accumulated == 1 else f"{turns_accumulated} turns"
    return (
        "[MEMORY SYNC - ROLLUP]\n"
        f"The following files have not been updated for {turn_scope}:\n{targets}\n\n"
        "You must persist the missing interactions now.\n"
        "For each listed target, write EXACTLY ONE rollup entry that summarizes\n"
        "all missing interactions in chronological order.\n"
        "Do not skip any interaction.\n\n"
        "Format for recent.md rollup:\n"
        "- Prefix: `[YYYY-MM-DD HH:MM] ` (use the latest turn timestamp in scope)\n"
        f"- Start entry body with `[rollup {turn_scope}]`\n"
        "- Include real names for people when applicable\n"
        "- Summarize what happened + your reaction in one coherent entry\n\n"
        "Call memory_edit now."
    )


def _sanitize_error_message(message: str) -> str:
    """Redact known sensitive tokens from surfaced error messages."""
    redacted = _SENSITIVE_URL_PARAM_RE.sub(r"\1***", message)
    return _GOOGLE_API_KEY_RE.sub("***", redacted)


def _rollback_turn_memory_changes(
    snapshot: _TurnMemorySnapshot,
    *,
    console: AgentUiPort,
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
    bm25_search: BM25MemorySearch | None = None,
    brain_has_vision: bool = False,
    use_own_vision_ability: bool = False,
    vision_agent: VisionAgent | None = None,
    gui_manager: GUIManager | None = None,
    gui_worker: GUIWorker | None = None,
    gui_lock: threading.Lock | None = None,
    screenshot_max_width: int | None = None,
    screenshot_quality: int = 80,
    contact_map: ContactMap | None = None,
    extra_allowed_paths: list[str] | None = None,
    on_shell_stdout_line: Callable[[str], None] | None = None,
    is_shell_cancel_requested: Callable[[], bool] | None = None,
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
        is_cancel_requested=is_shell_cancel_requested,
    )
    # When streaming is active, also transform collected stream-json
    # lines back into clean text for the tool result.
    _transform = (
        extract_text_from_claude_code_stream_json_lines
        if on_shell_stdout_line else None
    )
    base_execute_shell = create_execute_shell(
        executor,
        on_stdout_line=on_shell_stdout_line,
        output_transform=_transform,
    )

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
                allow_failure=tools_config.memory_search.agent.allow_failure,
            ),
            MEMORY_SEARCH_DEFINITION,
        )
    elif bm25_search is not None:
        registry.register(
            "memory_search",
            create_bm25_memory_search(bm25_search),
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

    # Screenshot tool -- mirrors read_image delegation pattern
    if brain_has_vision and not use_own_vision_ability and gui_worker is not None:
        # Delegate to GUIWorker sub-agent (avoids large image payloads)
        _crop_dir = str(agent_os_dir / "tmp")
        registry.register(
            "screenshot_by_subagent",
            create_screenshot_by_subagent(
                gui_worker,
                save_dir=_crop_dir,
                gui_lock=gui_lock,
            ),
            SCREENSHOT_BY_SUBAGENT_DEFINITION,
        )
        allowed_paths.append(_crop_dir)
    elif brain_has_vision:
        # Direct screenshot (brain processes image itself)
        registry.register(
            "screenshot",
            create_screenshot(
                max_width=screenshot_max_width,
                quality=screenshot_quality,
            ),
            SCREENSHOT_DEFINITION,
        )

    # NOTE: gui_task is registered after queue creation in app.py
    # (needs queue reference for background execution).

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
    console: AgentUiPort,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    memory_edit_allow_failure: bool = False,
    max_iterations: int = 10,
    memory_edit_turn_retry_limit: int = 3,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
    message_overlay: Callable[[list[Message]], list[Message]] | None = None,
    on_model_response: Callable[[LLMResponse], None] | None = None,
    thinking_channel: str | None = None,
    thinking_sender: str | None = None,
    tools_config: ToolsConfig | None = None,
) -> LLMResponse:
    """Run responder with tool call loop. Returns final response."""
    if message_overlay is not None:
        messages = message_overlay(messages)
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    with console.spinner():
        response = client.chat_with_tools(messages, tools)
    if on_model_response is not None:
        on_model_response(response)
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    _debug_print_responder_output(console, response, label="responder")
    _emit_reasoning_block_if_needed(
        console,
        response,
        channel=thinking_channel,
        sender=thinking_sender,
    )

    memory_edit_turn_fail_streak = 0
    iterations = 0
    while response.has_tool_calls():
        iterations += 1
        if iterations > max_iterations:
            logger.warning(
                "Responder loop exceeded %d iterations; breaking.",
                max_iterations,
            )
            console.print_warning(
                f"Tool loop exceeded {max_iterations} iterations; stopping.",
            )
            break
        chunk = response.content or ""
        if chunk.strip():
            console.print_assistant(chunk)

        conversation.add_assistant_with_tools(
            response.content,
            response.tool_calls,
            reasoning_content=response.reasoning_content,
            reasoning_details=response.reasoning_details,
        )

        failed_memory_edit_this_round = False
        memory_edit_failure_summaries: list[str] = []
        tool_results_this_round: dict[str, object] = {}
        for tool_call in response.tool_calls:
            _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
            if not registry.has_tool(tool_call.name):
                from ..tools.registry import ToolResult as _ToolResult

                result = _ToolResult(
                    f"Error: Unknown tool '{tool_call.name}'", is_error=True
                )
                conversation.add_tool_result(
                    tool_call.id, tool_call.name, result.content
                )
                tool_results_this_round[tool_call.id] = result
                continue
            console.print_tool_call(tool_call)
            if on_before_tool_call is not None:
                on_before_tool_call(tool_call)
            # gui_task returns instantly in background mode but retains
            # skip_spinner for synchronous fallback.  Claude Code
            # stream-json shell commands write to console while running.
            shell_command = tool_call.arguments.get("command")
            skip_spinner = (
                tool_call.name == "gui_task"
                or (
                    tool_call.name == "execute_shell"
                    and console.show_tool_use
                    and isinstance(shell_command, str)
                    and is_claude_code_stream_json_command(shell_command)
                )
            )
            if skip_spinner:
                result = registry.execute(tool_call)
            else:
                with console.spinner("Executing..."):
                    result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result.content)
            conversation.add_tool_result(tool_call.id, tool_call.name, result.content)
            tool_results_this_round[tool_call.id] = result
            _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
            if tool_call.name == "memory_edit" and isinstance(result.content, str) and is_failed_memory_edit_result(result.content):
                failed_memory_edit_this_round = True
                summary = summarize_memory_edit_failure(result.content)
                if summary:
                    memory_edit_failure_summaries.append(summary)

        if failed_memory_edit_this_round:
            memory_edit_turn_fail_streak += 1
            failure_detail = _format_memory_edit_failure_summaries(memory_edit_failure_summaries)
            if memory_edit_turn_fail_streak >= memory_edit_turn_retry_limit:
                if memory_edit_allow_failure:
                    console.print_warning(
                        "memory_edit turn-level retries exhausted"
                        f" ({failure_detail}); failed "
                        f"{memory_edit_turn_fail_streak} time(s); "
                        "allow_failure=true, continuing turn.",
                    )
                    break
                raise RuntimeError(
                    "memory_edit turn-level retries exhausted "
                    f"({failure_detail}); failed "
                    f"{memory_edit_turn_fail_streak} time(s); fail-closed for this turn."
                )
            console.print_warning(
                "memory_edit failed this round "
                f"({failure_detail}); retrying turn "
                f"({memory_edit_turn_fail_streak}/{memory_edit_turn_retry_limit})",
                indent=2,
            )
        else:
            memory_edit_turn_fail_streak = 0

        if _can_short_circuit_terminal_round(
            tool_calls=response.tool_calls,
            tool_results=tool_results_this_round,
            tools_config=tools_config,
        ):
            tool_names = [tc.name for tc in response.tool_calls]
            logger.info(
                "terminal_tool_short_circuit hit: tools=%s count=%d",
                ",".join(tool_names),
                len(tool_names),
            )
            if console.debug:
                console.print_debug(
                    "responder",
                    f"terminal_tool_short_circuit hit: tools=[{', '.join(tool_names)}]",
                )
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="terminal_tool_short_circuit",
            )

        messages = builder.build(conversation)
        if message_overlay is not None:
            messages = message_overlay(messages)
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        with console.spinner():
            response = client.chat_with_tools(messages, tools)
        if on_model_response is not None:
            on_model_response(response)
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        _debug_print_responder_output(console, response, label="responder")
        _emit_reasoning_block_if_needed(
            console,
            response,
            channel=thinking_channel,
            sender=thinking_sender,
        )

    return response


def _compose_message_overlays(
    first: Callable[[list[Message]], list[Message]] | None,
    second: Callable[[list[Message]], list[Message]] | None,
) -> Callable[[list[Message]], list[Message]] | None:
    """Compose two message overlays in order."""
    if first is None:
        return second
    if second is None:
        return first

    def _overlay(messages: list[Message]) -> list[Message]:
        return second(first(messages))

    return _overlay


def _load_plan_context_files(
    *,
    rel_paths: list[str],
    builder: ContextBuilder,
    console: AgentUiPort,
) -> list[tuple[str, str]]:
    """Load plan_context_files from agent_os_dir, warn on failure."""
    agent_os_dir = getattr(builder, "agent_os_dir", None)
    if not isinstance(agent_os_dir, Path):
        if rel_paths:
            console.print_warning(
                "plan_context_files unavailable: agent_os_dir is not set.",
                indent=2,
            )
        return []

    loaded: list[tuple[str, str]] = []
    for rel_path in rel_paths:
        try:
            content = (agent_os_dir / rel_path).read_text(encoding="utf-8")
            loaded.append((rel_path, content))
        except Exception as e:
            console.print_warning(
                f"plan_context_files: skipping {rel_path}: "
                f"{_sanitize_error_message(str(e))}",
                indent=2,
            )
    return loaded


def _run_brain_responder(
    *,
    client: LLMClient,
    messages: list[Message],
    tools: list[ToolDefinition],
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: AgentUiPort,
    config: AppConfig,
    channel: str,
    sender: str | None,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    memory_edit_allow_failure: bool = False,
    max_iterations: int = 10,
    memory_edit_turn_retry_limit: int = 3,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
    message_overlay: Callable[[list[Message]], list[Message]] | None = None,
    on_model_response: Callable[[LLMResponse], None] | None = None,
) -> LLMResponse:
    """Run the brain responder, optionally using staged planning."""
    tools_cfg = config.tools if isinstance(getattr(config, "tools", None), ToolsConfig) else None
    brain_cfg = config.agents.get("brain")
    staged = getattr(brain_cfg, "staged_planning", None)
    if staged is None or not staged.enabled:
        return _run_responder(
            client,
            messages,
            tools,
            conversation,
            builder,
            registry,
            console,
            on_before_tool_call=on_before_tool_call,
            memory_edit_allow_failure=memory_edit_allow_failure,
            max_iterations=max_iterations,
            memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
            is_cancel_requested=is_cancel_requested,
            on_cancel_pending=on_cancel_pending,
            message_overlay=message_overlay,
            on_model_response=on_model_response,
            thinking_channel=channel,
            thinking_sender=sender,
            tools_config=tools_cfg,
        )

    def _raise_cancel() -> None:
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)

    overlayed_messages = (
        list(message_overlay(messages)) if message_overlay is not None else list(messages)
    )
    stage1_max_iterations = max(1, min(staged.gather_max_iterations, max_iterations))

    # Skip memory_search gate when prior findings exist in conversation
    has_prior_findings = any(
        getattr(e, "name", None) == STAGE1_SYNTHETIC_TOOL_NAME
        for e in conversation.get_messages()
    )

    try:
        console.print_info("Stage 1/3: gather")
        stage1 = run_stage1_information_gathering(
            client=client,
            messages=overlayed_messages,
            all_tools=tools,
            registry=registry,
            console=console,
            raise_if_cancel_requested=_raise_cancel,
            max_iterations=stage1_max_iterations,
            skip_memory_search_gate=has_prior_findings,
        )
        if console.debug:
            console.print_debug(
                "staged-plan",
                f"stage1 tool_calls={stage1.tool_calls} transcript_chars={len(stage1.transcript)}",
            )

        # Persist Stage 1 findings in conversation for future turns
        if stage1.findings_text and stage1.findings_text != "(no stage1 tools available)":
            s1_call, s1_content = build_stage1_findings_for_conversation(
                stage1.findings_text,
            )
            conversation.add_assistant_with_tools(None, [s1_call])
            conversation.add_tool_result(s1_call.id, s1_call.name, s1_content)

        console.print_info("Stage 2/3: plan")
        stage2_messages = list(overlayed_messages)
        plan_context_loaded = _load_plan_context_files(
            rel_paths=staged.plan_context_files,
            builder=builder,
            console=console,
        )
        plan_context_msg = build_plan_context_message(plan_context_loaded)
        if plan_context_msg is not None:
            stage2_messages.append(plan_context_msg)
        stage2 = run_stage2_brain_planning(
            client=client,
            messages=stage2_messages,
            stage1=stage1,
            console=console,
            raise_if_cancel_requested=_raise_cancel,
        )
        if stage2 is None:
            console.print_warning(
                "Stage 2 planning failed; falling back to legacy responder loop.",
                indent=2,
            )
            return _run_responder(
                client,
                messages,
                tools,
                conversation,
                builder,
                registry,
                console,
                on_before_tool_call=on_before_tool_call,
                memory_edit_allow_failure=memory_edit_allow_failure,
                max_iterations=max_iterations,
                memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
                is_cancel_requested=is_cancel_requested,
                on_cancel_pending=on_cancel_pending,
                message_overlay=message_overlay,
                on_model_response=on_model_response,
                thinking_channel=channel,
                thinking_sender=sender,
                tools_config=tools_cfg,
            )
    except KeyboardInterrupt:
        raise
    except Exception as e:
        console.print_warning(
            "Staged planning failed; falling back to legacy responder loop: "
            f"{_sanitize_error_message(str(e))}",
            indent=2,
        )
        return _run_responder(
            client,
            messages,
            tools,
            conversation,
            builder,
            registry,
            console,
            on_before_tool_call=on_before_tool_call,
            memory_edit_allow_failure=memory_edit_allow_failure,
            max_iterations=max_iterations,
            memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
            is_cancel_requested=is_cancel_requested,
            on_cancel_pending=on_cancel_pending,
            message_overlay=message_overlay,
            on_model_response=on_model_response,
            thinking_channel=channel,
            thinking_sender=sender,
            tools_config=tools_cfg,
        )

    plan_text = format_stage2_plan_for_tui(stage2.plan_text)
    console.print_inner_thoughts(channel, sender, f"[PLAN][Stage2]\n{plan_text}")

    # Stage 3 overlay: findings + plan + context files
    stage3_overlay_msgs: list[Message] = [
        build_stage1_findings_overlay_message(stage1.findings_text),
        build_stage3_plan_overlay_message(stage2.plan_text),
    ]
    if plan_context_msg is not None:
        stage3_overlay_msgs.append(plan_context_msg)
    stage3_extra = _make_synthetic_message_overlay(stage3_overlay_msgs)
    stage3_overlay = _compose_message_overlays(message_overlay, stage3_extra)

    console.print_info("Stage 3/3: execute")
    response = _run_responder(
        client,
        messages,
        tools,
        conversation,
        builder,
        registry,
        console,
        on_before_tool_call=on_before_tool_call,
        memory_edit_allow_failure=memory_edit_allow_failure,
        max_iterations=max_iterations,
        memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
        is_cancel_requested=is_cancel_requested,
        on_cancel_pending=on_cancel_pending,
        message_overlay=stage3_overlay,
        on_model_response=on_model_response,
        thinking_channel=channel,
        thinking_sender=sender,
        tools_config=tools_cfg,
    )
    return response


def _format_memory_edit_failure_summaries(summaries: list[str]) -> str:
    """Format per-call memory_edit failure summaries for warning output."""
    if not summaries:
        return "unknown_failure"
    unique: list[str] = []
    for item in summaries:
        if item not in unique:
            unique.append(item)
    text = " | ".join(unique[:2])
    if len(unique) > 2:
        text += " | +"
    return text


def _run_memory_sync_side_channel(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    tools: list[ToolDefinition],
    registry: ToolRegistry,
    console: AgentUiPort,
    missing_targets: list[str],
    turns_accumulated: int = 1,
    max_retries: int = 1,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
) -> None:
    """Side-channel LLM call to sync missing memory targets.

    Builds a local copy of the conversation context, appends a memory-sync
    reminder, and calls the LLM.  Only memory_edit results are executed.
    On failure, retries up to max_retries times with error feedback.
    Full tool definitions are sent to maintain cache prefix parity with the
    main brain call (Anthropic caches: system -> tools -> messages).
    The main conversation is never modified.
    """
    if not any(d.name == "memory_edit" for d in tools):
        return

    local_messages = builder.build(conversation)
    local_messages.append(
        Message(
            role="user",
            content=_build_memory_sync_reminder(missing_targets, turns_accumulated),
        ),
    )

    for attempt in range(1 + max_retries):
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        with console.spinner():
            response = client.chat_with_tools(local_messages, tools)
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        _debug_print_responder_output(console, response, label="memory-sync")

        had_error = False
        for tool_call in response.tool_calls:
            if tool_call.name != "memory_edit":
                continue
            if not registry.has_tool(tool_call.name):
                continue
            console.print_tool_call(tool_call)
            if on_before_tool_call is not None:
                on_before_tool_call(tool_call)
            _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result.content)
            _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
            if result.is_error:
                had_error = True
                # Feed error back for retry
                local_messages.append(
                    Message(
                        role="assistant",
                        content=None,
                        tool_calls=[tool_call],
                    ),
                )
                local_messages.append(
                    Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    ),
                )

        if not had_error:
            break
        if attempt < max_retries:
            logger.info("memory-sync retry %d/%d after error", attempt + 1, max_retries)


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
    console: AgentUiPort,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
) -> str:
    """Side-channel LLM call to get a text response when responder returned empty.

    Builds a local copy of the conversation, appends a nudge prompt,
    and calls the LLM without tools to force a text reply.
    """
    local_messages = builder.build(conversation)
    local_messages.append(
        Message(role="user", content=_EMPTY_RESPONSE_NUDGE),
    )
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    with console.spinner():
        response = client.chat(local_messages)
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    if response and response.strip():
        return response
    return ""


def _run_memory_archive(
    agent_os_dir: Path,
    archive_config: MemoryArchiveConfig,
    console: AgentUiPort,
) -> None:
    """Run memory archive; log and swallow errors."""
    try:
        result = check_and_archive_buffers(agent_os_dir, archive_config)
        if result.archived:
            console.print_info(f"Memory archived: {result.summary}")
    except Exception as e:
        logger.warning("Memory archive failed: %s", e)



def _make_synthetic_message_overlay(
    extra_messages: list[Message] | tuple[Message, ...],
) -> Callable[[list[Message]], list[Message]]:
    """Return an overlay callback that appends synthetic context messages."""
    extras = tuple(extra_messages)

    def _overlay(messages: list[Message]) -> list[Message]:
        return [*messages, *extras]

    return _overlay


def _build_common_ground_overlay(
    *,
    shared_state_store: "SharedStateStore | None",
    config: AppConfig,
    turn_metadata: dict[str, object] | None,
    console: AgentUiPort,
    debug: bool,
) -> tuple[Callable[[list[Message]], list[Message]] | None, str | None]:
    """Build per-turn common-ground synthetic tool overlay when revisions diverge."""
    if shared_state_store is None:
        return None, None

    cg_cfg = config.context.common_ground
    if not cg_cfg.enabled or cg_cfg.mode != "auto_on_rev_mismatch":
        return None, None

    metadata = turn_metadata or {}
    scope_id = metadata.get("scope_id")
    anchor_shared_rev = metadata.get("anchor_shared_rev")
    if not isinstance(scope_id, str) or not scope_id:
        return None, None
    if not isinstance(anchor_shared_rev, int):
        return None, None

    turn_start_current_shared_rev = shared_state_store.get_current_rev(scope_id)
    if anchor_shared_rev > turn_start_current_shared_rev:
        console.print_warning(
            "common-ground skipped: cache underflow "
            f"(anchor={anchor_shared_rev} > current={turn_start_current_shared_rev})",
            indent=2,
        )
        if debug:
            console.print_debug(
                "common-ground",
                f"skip underflow scope={scope_id} anchor={anchor_shared_rev} current={turn_start_current_shared_rev}",
            )
        return None, None

    if anchor_shared_rev == turn_start_current_shared_rev:
        if debug:
            console.print_debug(
                "common-ground",
                f"no inject scope={scope_id} anchor=current={anchor_shared_rev}",
            )
        return None, scope_id

    pair = shared_state_store.build_common_ground_synthetic_messages(
        scope_id=scope_id,
        upto_rev=anchor_shared_rev,
        current_rev=turn_start_current_shared_rev,
        max_entries=cg_cfg.max_entries,
        max_chars=cg_cfg.max_chars,
        max_entry_chars=cg_cfg.max_entry_chars,
    )
    if pair is None:
        return None, scope_id

    if debug:
        tool_text = pair[1].content if isinstance(pair[1].content, str) else ""
        console.print_debug(
            "common-ground",
            f"injected scope={scope_id} anchor={anchor_shared_rev} current={turn_start_current_shared_rev} chars={len(tool_text)}",
        )
    return _make_synthetic_message_overlay(list(pair)), scope_id


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


class _MaintenanceScheduler:
    """Background timer that enqueues MaintenanceSentinel at daily_hour.

    Retries every retry_interval_minutes until latest_hour.
    Skips the day if latest_hour is passed without a successful run.
    """

    def __init__(
        self,
        queue: PersistentPriorityQueue,
        config: MaintenanceConfig,
        tz_name: str = "UTC+8",
    ):
        self._queue = queue
        self._config = config
        self._tz = parse_timezone_spec(tz_name)
        self._ran_today = False
        self._last_date: date | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def mark_done(self) -> None:
        """Called after successful maintenance to prevent re-trigger today."""
        self._ran_today = True

    def _loop_once(self) -> bool:
        """Check if maintenance is due. Returns True if sentinel enqueued."""
        now = datetime.now(self._tz)
        today = now.date()

        # Reset flag on new day
        if self._last_date != today:
            self._ran_today = False
            self._last_date = today

        if self._ran_today:
            return False

        hour = now.hour
        if hour < self._config.daily_hour:
            return False
        if hour >= self._config.latest_hour:
            # Past window; skip today
            self._ran_today = True
            logger.info("Maintenance window passed (%02d:00-%02d:00), skipping today",
                        self._config.daily_hour, self._config.latest_hour)
            return False

        self._queue.put(MaintenanceSentinel())
        return True

    def _loop(self) -> None:
        while not self._stop.wait(timeout=60):
            if self._loop_once():
                # Wait retry interval before next attempt
                self._stop.wait(timeout=self._config.retry_interval_minutes * 60)


class AgentCore:
    """Core agent logic: responder + memory sync."""

    def __init__(
        self,
        *,
        client: LLMClient,
        conversation: Conversation,
        builder: ContextBuilder,
        registry: ToolRegistry,
        ui_sink: UiSink,
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
        turn_cancel: object | None = None,
        shared_state_store: SharedStateStore | None = None,
        scope_resolver: ScopeResolver | None = None,
        memory_sync_client: LLMClient | None = None,
        ui_debug: bool = False,
        ui_show_tool_use: bool = False,
        ui_timezone: str | None = None,
        ui_gui_intent_max_chars: int | None = None,
    ):
        self.client = client
        self.memory_sync_client = memory_sync_client
        self.conversation = conversation
        self.builder = builder
        self.registry = registry
        self.ui_sink = ui_sink
        self.console: AgentUiPort = UiEventConsole(
            ui_sink,
            debug=ui_debug,
            show_tool_use=ui_show_tool_use,
        )
        if ui_timezone:
            self.console.set_timezone(ui_timezone)
        self.console.gui_intent_max_chars = ui_gui_intent_max_chars
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
        self.turn_cancel = turn_cancel
        self.shared_state_store = shared_state_store
        self.scope_resolver = scope_resolver or DEFAULT_SCOPE_RESOLVER
        self._refresh_timer: _RefreshTimer | None = None
        self._maintenance_scheduler: _MaintenanceScheduler | None = None
        self._turns_since_memory_sync: int = 0
        self.adapters: dict[str, ChannelAdapter] = {}
        brain_cfg = self.config.agents.get("brain")
        self._brain_provider = brain_cfg.llm.provider if brain_cfg is not None else ""
        self._soft_max_prompt_tokens = self.config.context.soft_max_prompt_tokens
        self._latest_token_status = _LatestTokenStatus()
        self._turn_token_usage = _TurnTokenUsage()

    def _reset_turn_token_usage(self) -> None:
        """Reset per-turn token aggregation state."""
        self._turn_token_usage = _TurnTokenUsage()

    def _record_brain_response_usage(self, response: LLMResponse) -> None:
        """Record usage from each brain model response in the current turn."""
        self._turn_token_usage.record(response)

    def _finalize_turn_token_status(self) -> None:
        """Publish per-turn aggregated usage to the status model."""
        agg = self._turn_token_usage
        if agg.usage_available:
            self._latest_token_status = _LatestTokenStatus(
                prompt_tokens=agg.max_prompt_tokens,
                completion_tokens=agg.completion_tokens_for_max_prompt,
                total_tokens=agg.total_tokens_for_max_prompt,
                usage_available=True,
                missing_usage=False,
            )
            return

        if self._brain_provider == "copilot" and agg.saw_missing_usage:
            self._latest_token_status = _LatestTokenStatus(
                usage_available=False,
                missing_usage=True,
            )

    def get_token_status_text(self) -> str:
        """Return token status text for toolbar and processing headers."""
        limit = self._soft_max_prompt_tokens
        state = self._latest_token_status
        if state.usage_available and state.prompt_tokens is not None:
            pct = state.prompt_tokens / limit * 100 if limit else 0
            suffix = " soft-over" if state.prompt_tokens > limit else ""
            return f"tok {state.prompt_tokens:,}/{limit:,} ({pct:.1f}%){suffix}"
        if state.missing_usage:
            return f"tok unavailable/{limit:,} (copilot no usage)"
        return f"tok --/{limit:,} (--.-%)"

    def _apply_soft_prompt_compaction(self) -> None:
        """Compact history after a turn when soft token budget is exceeded."""
        state = self._latest_token_status
        if not state.usage_available:
            return
        prompt_tokens = state.prompt_tokens
        if prompt_tokens is None or prompt_tokens <= self._soft_max_prompt_tokens:
            return
        removed = self.conversation.compact(self.config.context.preserve_turns)
        if removed <= 0:
            return
        if self.session_mgr is not None:
            self.session_mgr.rewrite_messages(self.conversation.get_messages())
        self.console.print_warning(
            "Soft token limit exceeded "
            f"({prompt_tokens:,}/{self._soft_max_prompt_tokens:,}); "
            f"compacted {removed} messages.",
            indent=2,
        )

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

        Handles ContextLengthExceededError (emergency compact + single retry),
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
        turn_metadata = dict(self.turn_context.metadata) if self.turn_context is not None else None
        self.conversation.add(
            "user",
            user_input,
            channel=channel,
            sender=sender,
            timestamp=timestamp,
            metadata=turn_metadata,
        )
        messages = self.builder.build(self.conversation)
        cg_scope_id = turn_metadata.get("scope_id") if isinstance(turn_metadata, dict) else None
        cg_anchor_rev = turn_metadata.get("anchor_shared_rev") if isinstance(turn_metadata, dict) else None
        cg_turn_start_current_rev: int | None = None
        if (
            getattr(self, "shared_state_store", None) is not None
            and isinstance(cg_scope_id, str)
            and cg_scope_id
        ):
            cg_turn_start_current_rev = self.shared_state_store.get_current_rev(cg_scope_id)
        common_ground_overlay, _common_ground_scope = _build_common_ground_overlay(
            shared_state_store=getattr(self, "shared_state_store", None),
            config=self.config,
            turn_metadata=turn_metadata,
            console=self.console,
            debug=debug,
        )
        if debug:
            if not self.config.context.common_ground.enabled:
                self.console.print_debug("common-ground-turn", "disabled")
            elif not isinstance(cg_scope_id, str) or not cg_scope_id:
                self.console.print_debug("common-ground-turn", "skip no_scope")
            elif not isinstance(cg_anchor_rev, int):
                self.console.print_debug("common-ground-turn", f"skip no_anchor scope={cg_scope_id}")
            elif cg_turn_start_current_rev is None:
                self.console.print_debug(
                    "common-ground-turn",
                    f"skip no_store scope={cg_scope_id} anchor={cg_anchor_rev}",
                )
            else:
                self.console.print_debug(
                    "common-ground-turn",
                    "injected="
                    f"{common_ground_overlay is not None} "
                    f"scope={cg_scope_id} "
                    f"anchor={cg_anchor_rev} "
                    f"current={cg_turn_start_current_rev}",
                )

        if debug:
            # Show the last user message as seen by LLM (with timestamp prefix)
            for m in reversed(messages):
                if m.role == "user" and isinstance(m.content, str):
                    self.console.print_debug("context", m.content[:200])
                    break

        turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
        turn_anchor = len(self.conversation.get_messages())

        try:
            tools = self.registry.get_definitions()
            _is_cancel = getattr(self.turn_cancel, "is_requested", None)
            _cancel_pending = getattr(self.turn_cancel, "mark_pending", None)

            # === Responder ===
            self._reset_turn_token_usage()
            response = _run_brain_responder(
                client=self.client,
                messages=messages,
                tools=tools,
                conversation=self.conversation,
                builder=self.builder,
                registry=self.registry,
                console=self.console,
                config=self.config,
                channel=channel,
                sender=sender,
                on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                memory_edit_allow_failure=self.memory_edit_allow_failure,
                max_iterations=self.config.tools.max_tool_iterations,
                memory_edit_turn_retry_limit=self.config.tools.memory_edit.turn_retry_limit,
                is_cancel_requested=_is_cancel,
                on_cancel_pending=_cancel_pending,
                message_overlay=common_ground_overlay,
                on_model_response=self._record_brain_response_usage,
            )
            self._finalize_turn_token_status()
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
            # Tracks consecutive non-heartbeat turns where the agent did not
            # naturally update memory targets (e.g. recent.md).  When the
            # counter reaches every_n_turns, forces one sync call, then resets.
            # every_n_turns=null disables forced sync entirely.
            is_system_heartbeat = (
                self.turn_context is not None
                and self.turn_context.metadata.get("system")
            )
            sync_cfg = self.config.tools.memory_sync
            should_sync = False
            if not is_system_heartbeat and sync_cfg.every_n_turns is not None:
                sync_turn_messages = self.conversation.get_messages()[turn_anchor:]
                missing = find_missing_memory_sync_targets(sync_turn_messages)
                if not missing:
                    # Agent updated targets naturally this turn
                    self._turns_since_memory_sync = 0
                else:
                    self._turns_since_memory_sync += 1
                    if self._turns_since_memory_sync >= sync_cfg.every_n_turns:
                        should_sync = True
                if debug:
                    self.console.print_debug(
                        "memory-sync",
                        f"missing={bool(missing)}, "
                        f"counter={self._turns_since_memory_sync}/{sync_cfg.every_n_turns}",
                    )
            elif debug:
                reason = "heartbeat" if is_system_heartbeat else "disabled"
                self.console.print_debug("memory-sync", f"skipped: {reason}")

            if should_sync:
                try:
                    sync_client = getattr(self, "memory_sync_client", None) or self.client
                    if debug:
                        dispatch = "memory_sync" if sync_client is not self.client else "brain"
                        self.console.print_debug("memory-sync", f"dispatch client={dispatch}")
                    _run_memory_sync_side_channel(
                        sync_client, self.conversation, self.builder,
                        tools, self.registry, self.console,
                        missing_targets=missing,  # type: ignore[possibly-undefined]
                        turns_accumulated=self._turns_since_memory_sync,
                        max_retries=sync_cfg.max_retries,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        is_cancel_requested=_is_cancel,
                        on_cancel_pending=_cancel_pending,
                    )
                    self._turns_since_memory_sync = 0
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

            self._apply_soft_prompt_compaction()

            # Post-turn hooks removed: archive/backup handled by daily maintenance.
            # Only overflow guard (ContextLengthExceededError) still archives.

        except ContextLengthExceededError:
            _rollback_turn_memory_changes(
                turn_memory_snapshot, console=self.console, debug=debug,
            )
            self.conversation._messages = self.conversation._messages[:pre_turn_anchor]

            # Archive to shrink boot files (e.g. recent.md), then reload
            # so builder picks up the smaller content for the retry.
            _run_memory_archive(
                self.agent_os_dir, self.config.maintenance.archive, self.console,
            )
            self.builder.reload_boot_files()
            keep_turns = self.config.context.overflow_retry_keep_turns
            removed = self.conversation.compact(keep_turns)
            if self.session_mgr is not None and removed > 0:
                self.session_mgr.rewrite_messages(self.conversation.get_messages())
            self.console.print_warning(
                "Token limit exceeded. "
                f"Compacting to {keep_turns} turns and retrying once...",
            )

            self.conversation.add(
                "user",
                user_input,
                channel=channel,
                sender=sender,
                metadata=turn_metadata,
            )
            messages = self.builder.build(self.conversation)
            turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
            try:
                tools = self.registry.get_definitions()
                turn_anchor = len(self.conversation.get_messages())
                self._reset_turn_token_usage()
                response = _run_brain_responder(
                    client=self.client,
                    messages=messages,
                    tools=tools,
                    conversation=self.conversation,
                    builder=self.builder,
                    registry=self.registry,
                    console=self.console,
                    config=self.config,
                    channel=channel,
                    sender=sender,
                    on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                    memory_edit_allow_failure=self.memory_edit_allow_failure,
                    max_iterations=self.config.tools.max_tool_iterations,
                    memory_edit_turn_retry_limit=self.config.tools.memory_edit.turn_retry_limit,
                    is_cancel_requested=_is_cancel,
                    on_cancel_pending=_cancel_pending,
                    message_overlay=common_ground_overlay,
                    on_model_response=self._record_brain_response_usage,
                )
                self._finalize_turn_token_status()
                final_content, used_fallback_content = _resolve_final_content(
                    response.content,
                    self.conversation.get_messages()[turn_anchor:],
                )
                final_content = _strip_timestamp_prefix(final_content)
                if final_content and not used_fallback_content:
                    self.conversation.add("assistant", final_content)
                _output(final_content or None)
                self._apply_soft_prompt_compaction()
            except ContextLengthExceededError:
                _rollback_turn_memory_changes(
                    turn_memory_snapshot, console=self.console, debug=debug,
                )
                self.conversation._messages = self.conversation._messages[:pre_turn_anchor]
                self.console.print_error(
                    "Context still too large after emergency overflow compaction."
                )
            except Exception as e:
                _rollback_turn_memory_changes(
                    turn_memory_snapshot, console=self.console, debug=debug,
                )
                self.console.print_error(_sanitize_error_message(str(e)))
                self.conversation._messages = self.conversation._messages[:pre_turn_anchor]

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
            pass

    def graceful_exit(self) -> None:
        """Handle graceful exit.

        Keeps finalize + archive only; backup and session cleanup are
        handled by the daily maintenance window.
        """
        if self.session_mgr is not None:
            self.session_mgr.finalize("completed")

        if self.agent_os_dir and self.config:
            _run_memory_archive(
                self.agent_os_dir, self.config.maintenance.archive, self.console,
            )

        self.console.print_goodbye()

    def _perform_context_refresh(self, preserve_turns: int | None = None) -> None:
        """Compact conversation, reload boot files, rotate session."""
        cfg = self._context_refresh_config
        turns = preserve_turns
        if turns is None and cfg is not None:
            turns = cfg.preserve_turns
        if turns is None:
            turns = 2

        try:
            # 1. Compact conversation
            removed = self.conversation.compact(turns)

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

    def _perform_maintenance(self) -> None:
        """Run daily maintenance: archive -> refresh -> backup -> session_cleanup."""
        cfg = self.config.maintenance if self.config else None
        if cfg is None or not cfg.enabled:
            return

        logger.info("Daily maintenance started")
        try:
            # 1. Archive
            _run_memory_archive(
                self.agent_os_dir, cfg.archive, self.console,
            )

            # 2. Context refresh (compact + reload + session rotate)
            self._perform_context_refresh(
                preserve_turns=cfg.refresh_preserve_turns,
            )

            # 3. Backup (force=True: maintenance always backs up regardless of interval)
            if cfg.backup.enabled and self.memory_backup_mgr:
                try:
                    self.memory_backup_mgr.check_and_backup(force=True)
                except Exception as e:
                    logger.warning("Maintenance backup failed: %s", e)

            # 4. Session cleanup
            if cfg.session_cleanup.enabled and self.agent_os_dir:
                try:
                    from ..session.cleanup import cleanup_sessions
                    cleanup_sessions(
                        self.agent_os_dir / "session",
                        retention_days=cfg.session_cleanup.retention_days,
                    )
                except Exception as e:
                    logger.warning("Maintenance session cleanup failed: %s", e)

            # Mark scheduler so it doesn't re-trigger today
            if self._maintenance_scheduler:
                self._maintenance_scheduler.mark_done()

            self.console.print_info("Daily maintenance completed.")
        except Exception as e:
            logger.warning("Daily maintenance failed: %s", e)

    def _schedule_next_heartbeat(self, msg: InboundMessage) -> None:
        """Create the next recurring heartbeat after a successful turn."""
        from .adapters.scheduler import make_heartbeat_message, random_delay

        recur_spec = msg.metadata.get("recur_spec", "2h-5h")
        try:
            delay = random_delay(recur_spec)
        except ValueError:
            logger.warning("Invalid recur_spec %r; using default 2h-5h", recur_spec)
            delay = random_delay("2h-5h")

        next_time = self._apply_quiet_hours(datetime.now(timezone.utc) + delay)
        next_msg = make_heartbeat_message(
            not_before=next_time,
            interval_spec=recur_spec,
            timezone=self.config.timezone,
        )
        self._queue.put(next_msg)
        delay_min = (next_time - datetime.now(timezone.utc)).total_seconds() / 60
        if delay_min >= 120:
            logger.info("Next heartbeat in %.1fh", delay_min / 60)
        else:
            logger.info("Next heartbeat in %.0fm", delay_min)

    def _defer_pending_heartbeat(self) -> None:
        """Push back pending heartbeat after a non-heartbeat turn.

        Resets the heartbeat timer using the same interval spec so the
        agent does not wake up immediately after real activity.
        """
        from .adapters.scheduler import make_heartbeat_message, random_delay

        for filepath, msg in self._queue.scan_pending(channel="system"):
            if not msg.metadata.get("system") or not msg.metadata.get("recurring"):
                continue
            # Found the pending heartbeat; remove and re-create with fresh delay
            recur_spec = msg.metadata.get("recur_spec")
            if not recur_spec:
                adapter = self.adapters.get("system")
                recur_spec = getattr(adapter, "_interval", None) or "2h-5h"
            self._queue.remove_pending(filepath)
            delay = random_delay(recur_spec)
            next_time = self._apply_quiet_hours(datetime.now(timezone.utc) + delay)
            next_msg = make_heartbeat_message(
                not_before=next_time,
                interval_spec=recur_spec,
                timezone=self.config.timezone,
            )
            self._queue.put(next_msg)
            delay_min = (next_time - datetime.now(timezone.utc)).total_seconds() / 60
            if delay_min >= 120:
                logger.info("Deferred heartbeat by %.1fh", delay_min / 60)
            else:
                logger.info("Deferred heartbeat by %.0fm", delay_min)
            break  # Only one heartbeat at a time

    def _apply_quiet_hours(self, dt: datetime) -> datetime:
        """Push *dt* past quiet hours if it falls within a blackout window."""
        from ..core.schema import is_in_quiet_hours, next_quiet_end
        from ..timezone_utils import parse_timezone_spec

        windows = self.config.heartbeat.parsed_quiet_windows()
        if not windows:
            return dt
        tz = parse_timezone_spec(self.config.timezone)
        if is_in_quiet_hours(dt, windows, tz):
            end = next_quiet_end(dt, windows, tz)
            logger.info("Heartbeat deferred past quiet hours to %s", end.astimezone(tz))
            return end
        return dt

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
        if isinstance(msg, InboundMessage):
            shared_state_store = getattr(self, "shared_state_store", None)
            scope_resolver = getattr(self, "scope_resolver", None)
            if shared_state_store is not None and scope_resolver is not None:
                scope_id = scope_resolver.inbound(msg)
                if scope_id:
                    msg.metadata = dict(msg.metadata)
                    msg.metadata["scope_id"] = scope_id
                    msg.metadata["anchor_shared_rev"] = shared_state_store.get_current_rev(scope_id)
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

        # Start context refresh timer if configured (legacy)
        if self._context_refresh_config and self._context_refresh_config.enabled:
            self._refresh_timer = _RefreshTimer(
                self._queue, self._context_refresh_config,
            )
            self._refresh_timer.start()

        # Start daily maintenance scheduler
        maint_cfg = self.config.maintenance if self.config else None
        if maint_cfg and maint_cfg.enabled:
            tz_name = self.config.timezone if self.config else "UTC+8"
            self._maintenance_scheduler = _MaintenanceScheduler(
                self._queue, maint_cfg, tz_name=tz_name,
            )
            self._maintenance_scheduler.start()

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
                if isinstance(msg, MaintenanceSentinel):
                    if self._queue.pending_count() == 0:
                        self._perform_maintenance()
                    continue
                self._process_inbound(msg, receipt)
        except KeyboardInterrupt:
            self.graceful_exit()
        finally:
            self._queue.stop_promotion()
            if self._refresh_timer:
                self._refresh_timer.stop()
            if self._maintenance_scheduler:
                self._maintenance_scheduler.stop()
            for adapter in self.adapters.values():
                adapter.stop()

    def _process_inbound(self, msg: InboundMessage, receipt: Path | None) -> None:
        """Process one inbound message through the turn pipeline."""
        # Update turn context before notifying adapters so channel-specific
        # adapters (e.g. Discord typing indicators) can inspect inbound metadata.
        if self.turn_context is not None:
            self.turn_context.set_inbound(msg.channel, msg.sender, msg.metadata)

        # Notify all adapters so terminal-owning ones (CLI) can suspend
        for a in self.adapters.values():
            a.on_turn_start(msg.channel)

        self.console.print_inbound(
            msg.channel, msg.sender, msg.content, ts=msg.timestamp,
        )
        self.console.print_processing(msg.channel, msg.sender)

        # Inner thoughts callback: display on console only, never sent.
        # Actual message delivery happens via the send_message tool.
        def _thoughts(content: str | None) -> None:
            self.console.print_inner_thoughts(msg.channel, msg.sender, content)

        completed = False
        pre_turn_len = len(self.conversation.get_messages())
        try:
            self.run_turn(
                msg.content, output_fn=_thoughts,
                channel=msg.channel, sender=msg.sender,
                timestamp=msg.timestamp,
            )
            completed = True
        finally:
            had_turn_context = self.turn_context is not None
            had_send_message = False
            if self.turn_context is not None:
                had_send_message = bool(self.turn_context.sent_hashes)
                self.turn_context.clear()

            turn_messages = self.conversation.get_messages()[pre_turn_len:]
            is_heartbeat_like = bool(msg.metadata.get("system"))
            is_scheduled = (
                msg.channel == "system"
                and "scheduled_reason" in msg.metadata
            )
            is_discord_review = (
                msg.channel == "discord"
                and msg.metadata.get("source") in {
                    "guild_review",
                    "guild_mention_review",
                }
            )

            should_evict = False
            evict_reason = ""
            if completed and had_turn_context:
                if is_heartbeat_like and not had_send_message:
                    should_evict = True
                    evict_reason = "silent heartbeat/startup"
                elif is_scheduled:
                    effects = analyze_turn_effects(
                        turn_messages,
                        had_send_message=had_send_message,
                    )
                    if effects.is_scheduled_noop:
                        should_evict = True
                        evict_reason = "noop scheduled turn"
                elif is_discord_review and not had_send_message:
                    effects = analyze_turn_effects(
                        turn_messages,
                        had_send_message=had_send_message,
                    )
                    if effects.is_scheduled_noop:
                        should_evict = True
                        evict_reason = "noop discord review turn"

            if should_evict:
                evicted = len(self.conversation._messages) - pre_turn_len
                self.conversation._messages = (
                    self.conversation._messages[:pre_turn_len]
                )
                logger.debug(
                    "Evicted %s (%d messages)", evict_reason, evicted,
                )
            if self._queue is not None and completed:
                self._queue.ack(receipt)
                # Auto-schedule next heartbeat for recurring messages
                if msg.metadata.get("recurring"):
                    self._schedule_next_heartbeat(msg)
                else:
                    self._defer_pending_heartbeat()
            for a in self.adapters.values():
                a.on_turn_complete()
