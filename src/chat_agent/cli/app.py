from fnmatch import fnmatch
from pathlib import Path
import json
import re

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import ToolsConfig
from ..llm import LLMResponse, create_client
from ..llm.base import LLMClient
from ..llm.schema import Message, ToolCall, ToolDefinition
from ..memory_writer import MemoryWriter, SessionCommitLog
from ..reviewer import PreReviewer, PostReviewer, RequiredAction
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
    MEMORY_EDIT_DEFINITION,
    create_read_file,
    create_write_file,
    create_edit_file,
    create_memory_edit,
)
from .console import ChatConsole
from .input import ChatInput
from .commands import CommandHandler, CommandResult
from .shutdown import perform_shutdown, _has_conversation_content


def _collect_turn_tool_calls(turn_messages: list[Message]) -> list[ToolCall]:
    """Collect all tool calls made in a single responder attempt."""
    tool_calls: list[ToolCall] = []
    for msg in turn_messages:
        if msg.role == "assistant" and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    return tool_calls


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


def _extract_memory_edit_paths(tool_call: ToolCall) -> list[str]:
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


def _is_memory_edit_index_update(tool_call: ToolCall, index_path: str) -> bool:
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


def _build_memory_shell_write_patterns(working_dir: Path) -> list[re.Pattern[str]]:
    """Build shell patterns that indicate direct memory writes."""
    memory_abs = re.escape(str((working_dir / "memory").resolve()))
    memory_rel = r"(?:\./)?(?:\.agent/)?memory/"
    memory_target = rf"(?:['\"])?(?:{memory_rel}|{memory_abs}/)"
    return [
        re.compile(rf">>?\s*{memory_target}"),
        re.compile(rf"\btee(?:\s+-a)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\bsed\s+-i(?:\S*)?\b[^\n]*\s{memory_target}"),
    ]


def _is_memory_write_shell_command(command: str, *, working_dir: Path) -> bool:
    """Check if command contains shell patterns that write under memory/."""
    return any(
        pattern.search(command) is not None
        for pattern in _build_memory_shell_write_patterns(working_dir)
    )


def _is_failed_memory_edit_result(result: str) -> bool:
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


def _match_path(path: str, action: RequiredAction) -> bool:
    """Check whether a tool-call path satisfies the action target constraints."""
    if not action.target_path and not action.target_path_glob:
        return True
    if action.target_path and path == action.target_path:
        return True
    if action.target_path_glob and fnmatch(path, action.target_path_glob):
        return True
    return False


def _match_action_call(tool_call: ToolCall, action: RequiredAction) -> bool:
    """Check whether one tool call satisfies one required action."""
    if action.tool == "write_or_edit":
        if tool_call.name not in {"write_file", "edit_file", "memory_edit"}:
            return False
    elif action.tool == "memory_edit":
        if tool_call.name != "memory_edit":
            return False
        if not action.target_path and not action.target_path_glob:
            return True
        return any(_match_path(path, action) for path in _extract_memory_edit_paths(tool_call))
    elif tool_call.name != action.tool:
        return False

    if action.tool in {"write_file", "edit_file", "write_or_edit", "read_file"}:
        if tool_call.name == "memory_edit":
            return any(_match_path(path, action) for path in _extract_memory_edit_paths(tool_call))
        path = str(tool_call.arguments.get("path", ""))
        return _match_path(path, action)

    if action.tool == "execute_shell":
        command = str(tool_call.arguments.get("command", ""))
        if action.command_must_contain and action.command_must_contain not in command:
            return False
        return True

    if action.tool == "get_current_time":
        return True

    return False


def _is_action_satisfied(tool_calls: list[ToolCall], action: RequiredAction) -> bool:
    """Verify action completion, including mandatory index update when required."""
    primary_ok = any(_match_action_call(tc, action) for tc in tool_calls)
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
            and _is_memory_edit_index_update(tc, action.index_path)
        )
        for tc in tool_calls
    )


def _find_missing_actions(
    turn_messages: list[Message],
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Return required actions that were not completed in this attempt."""
    if not required_actions:
        return []

    tool_calls = _collect_turn_tool_calls(turn_messages)
    return [a for a in required_actions if not _is_action_satisfied(tool_calls, a)]


def _has_memory_write(turn_messages: list[Message]) -> bool:
    """Check whether this responder attempt wrote any memory file."""
    for tool_call in _collect_turn_tool_calls(turn_messages):
        if tool_call.name in {"write_file", "edit_file"}:
            path = str(tool_call.arguments.get("path", ""))
            if path.startswith("memory/"):
                return True
            continue

        if tool_call.name == "memory_edit":
            for path in _extract_memory_edit_paths(tool_call):
                if path.startswith("memory/"):
                    return True
    return False


_IDENTITY_SYNC_PATHS = (
    "memory/agent/persona.md",
    "memory/agent/config.md",
)
_IDENTITY_PATH_MARKERS = (
    "identity",
    "persona",
    "rebirth",
    "rename",
    "naming",
    "independence",
)
_IDENTITY_TEXT_MARKERS = (
    "identity",
    "persona",
    "rename",
    "renamed",
    "rebirth",
    "reborn",
    "independent",
    "new name",
)


def _action_targets_identity_sync(action: RequiredAction) -> bool:
    """Check whether action already covers identity sync files."""
    if action.target_path and action.target_path in _IDENTITY_SYNC_PATHS:
        return True
    if action.target_path_glob:
        return any(
            fnmatch(path, action.target_path_glob)
            for path in _IDENTITY_SYNC_PATHS
        )
    return False


def _has_identity_sync_write(turn_messages: list[Message]) -> bool:
    """Return True when the current turn already writes persona/config."""
    for tool_call in _collect_turn_tool_calls(turn_messages):
        if tool_call.name in {"write_file", "edit_file"}:
            path = str(tool_call.arguments.get("path", ""))
            if path in _IDENTITY_SYNC_PATHS:
                return True
            continue

        if tool_call.name == "memory_edit":
            if any(
                path in _IDENTITY_SYNC_PATHS
                for path in _extract_memory_edit_paths(tool_call)
            ):
                return True
    return False


def _has_identity_memory_signal(turn_messages: list[Message]) -> bool:
    """Detect identity-related memory updates from memory_edit payloads."""
    for tool_call in _collect_turn_tool_calls(turn_messages):
        if tool_call.name != "memory_edit":
            continue

        requests = tool_call.arguments.get("requests", [])
        if not isinstance(requests, list):
            continue

        for request in requests:
            if not isinstance(request, dict):
                continue

            path_blob = " ".join(
                str(request.get(field, "")).lower()
                for field in ("target_path", "index_path", "link_path")
            )
            if any(marker in path_blob for marker in _IDENTITY_PATH_MARKERS):
                return True

            text_blob = " ".join(
                str(request.get(field, "")).lower()
                for field in ("payload_text", "item_text", "link_title", "section_hint")
            )
            if any(marker in text_blob for marker in _IDENTITY_TEXT_MARKERS):
                return True

    return False


def _needs_identity_sync_action(turn_messages: list[Message]) -> bool:
    """Check whether identity-related updates require persona/config sync."""
    if not _has_memory_write(turn_messages):
        return False
    if _has_identity_sync_write(turn_messages):
        return False
    return _has_identity_memory_signal(turn_messages)


def _build_identity_sync_action() -> RequiredAction:
    """Build deterministic action for syncing persona after identity updates."""
    return RequiredAction(
        code="sync_identity_persona",
        description=(
            "Identity-related memory updates were detected. "
            "Sync memory/agent/persona.md in this turn."
        ),
        tool="memory_edit",
        target_path="memory/agent/persona.md",
    )


def _ensure_identity_sync_action(
    required_actions: list[RequiredAction],
    turn_messages: list[Message],
) -> list[RequiredAction]:
    """Append persona sync action when identity updates are present."""
    if not _needs_identity_sync_action(turn_messages):
        return required_actions

    for action in required_actions:
        if _action_targets_identity_sync(action):
            return required_actions

    return [*required_actions, _build_identity_sync_action()]


def _collect_required_actions_for_retry(
    turn_messages: list[Message],
    *,
    passed: bool,
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Select required actions that still need retry for this attempt."""
    missing_actions = _find_missing_actions(turn_messages, required_actions)
    if required_actions and missing_actions:
        return missing_actions
    if not passed and required_actions and not missing_actions:
        return []
    if not passed:
        return required_actions
    return []


def _build_turn_persistence_action() -> RequiredAction:
    """Build fallback action to force minimum per-turn memory persistence."""
    return RequiredAction(
        code="persist_turn_memory",
        description=(
            "Persist this turn to rolling memory via memory/short-term.md "
            "before finalizing the user-facing answer."
        ),
        tool="memory_edit",
        target_path="memory/short-term.md",
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


def _build_retry_reminder(
    retry_instruction: str,
    required_actions: list[RequiredAction],
) -> str:
    """Build a strict and structured retry reminder from required actions."""
    lines = [
        "COMPLIANCE RETRY: Your previous response failed post-review.",
        "Complete EVERY required action below before finalizing your response.",
        "Call tools first, then give the final user-facing answer.",
        "",
        "Required actions:",
    ]

    for i, action in enumerate(required_actions, start=1):
        parts = [f"{i}. [{action.code}] {action.description}"]
        parts.append(f"   - tool: {action.tool}")
        if action.target_path:
            parts.append(f"   - target_path: {action.target_path}")
        if action.target_path_glob:
            parts.append(f"   - target_path_glob: {action.target_path_glob}")
        if action.command_must_contain:
            parts.append(f"   - command_must_contain: {action.command_must_contain}")
        if action.index_path:
            parts.append(f"   - also_update_index: {action.index_path}")
        if action.tool == "memory_edit":
            sample_target = action.target_path or "memory/short-term.md"
            parts.append("   - use exact keys: as_of, turn_id, requests")
            parts.append("   - memory_edit minimal payload:")
            parts.append(
                "     "
                + (
                    '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
                    '"requests":[{"request_id":"r1","kind":"append_entry",'
                    f'"target_path":"{sample_target}",'
                    '"payload_text":"<entry>"}]}'
                )
            )
        lines.extend(parts)

    if retry_instruction:
        lines.extend(["", "Reviewer instruction:", retry_instruction])

    return "\n".join(lines)


def _action_signature(
    required_actions: list[RequiredAction],
    violations: list[str],
) -> tuple[str, ...]:
    """Build stable signature for retry loop guard."""
    if required_actions:
        return tuple(sorted(a.code for a in required_actions))
    return tuple(sorted(v.lower() for v in violations))


def _build_reviewer_warning(stage: str, raw_response: str | None) -> str:
    """Build human-readable warning when a reviewer pass fails."""
    if raw_response is None:
        return (
            f"{stage} failed due to model call error; skipping this pass for current turn."
        )
    return (
        f"{stage} returned invalid JSON/schema; skipping this pass for current turn."
    )


def setup_tools(
    tools_config: ToolsConfig,
    working_dir: Path,
    *,
    memory_writer: MemoryWriter | None = None,
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

    if memory_writer is not None:
        registry.register(
            "memory_edit",
            create_memory_edit(
                memory_writer,
                allowed_paths=allowed_paths,
                base_dir=working_dir,
            ),
            MEMORY_EDIT_DEFINITION,
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
) -> LLMResponse:
    """Run responder with tool call loop. Returns final response."""
    with console.spinner():
        response = client.chat_with_tools(messages, tools)

    while response.has_tool_calls():
        conversation.add_assistant_with_tools(response.content, response.tool_calls)

        for tool_call in response.tool_calls:
            console.print_tool_call(tool_call)
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)
            if tool_call.name == "memory_edit" and _is_failed_memory_edit_result(result):
                raise RuntimeError(
                    "memory_edit failed; fail-closed for this turn."
                )

        messages = builder.build(conversation)
        with console.spinner():
            response = client.chat_with_tools(messages, tools)

    # Some tool-capable models may return empty final content after tool loop.
    # Ask once without tools for a clean natural-language final reply.
    if not (response.content or "").strip():
        messages = builder.build(conversation)
        with console.spinner():
            fallback_content = client.chat(messages)
        if fallback_content.strip():
            response = LLMResponse(content=fallback_content, tool_calls=[])

    return response


def _graceful_exit(
    client,
    conversation,
    builder,
    registry,
    console,
    workspace,
    user_id,
    shutdown_reviewer=None,
    shutdown_reviewer_max_retries: int = 0,
    shutdown_reviewer_warn_on_failure: bool = True,
):
    """Handle graceful exit with optional memory saving."""
    if _has_conversation_content(conversation):
        try:
            shutdown_ok = perform_shutdown(
                client, conversation, builder, registry,
                console, workspace, user_id,
                reviewer=shutdown_reviewer,
                reviewer_max_retries=shutdown_reviewer_max_retries,
                reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
            )
            if not shutdown_ok:
                console.print_error(
                    "Shutdown memory persistence failed (fail-closed)."
                )
        except KeyboardInterrupt:
            console.print_info("Shutdown interrupted.")
        except Exception as e:
            console.print_error(f"Failed to save memories: {e}")
    console.print_goodbye()


def main(user: str) -> None:
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
        system_prompt = workspace.get_system_prompt("brain", current_user=user_id)
    except FileNotFoundError as e:
        console.print_error(f"Failed to load system prompt: {e}")
        return
    except ValueError as e:
        console.print_error(str(e))
        return

    debug = config.debug
    console.set_debug(debug)
    global_warn_on_failure = config.warn_on_failure

    brain_agent_config = config.agents["brain"]
    client = create_client(
        brain_agent_config.llm,
        timeout_retries=brain_agent_config.llm_timeout_retries,
        request_timeout=brain_agent_config.llm_request_timeout,
    )

    if "memory_writer" not in config.agents:
        console.print_error("agents.memory_writer is required for memory persistence.")
        return

    memory_writer_config = config.agents["memory_writer"]
    memory_writer_client = create_client(
        memory_writer_config.llm,
        timeout_retries=memory_writer_config.llm_timeout_retries,
        request_timeout=memory_writer_config.llm_request_timeout,
    )
    try:
        memory_writer_system_prompt = workspace.get_system_prompt(
            "memory_writer",
            current_user=user_id,
        )
        memory_writer_parse_retry_prompt = workspace.get_agent_prompt(
            "memory_writer",
            "parse-retry",
            current_user=user_id,
        )
    except FileNotFoundError as e:
        console.print_error(f"Failed to load memory writer prompt: {e}")
        return
    except ValueError as e:
        console.print_error(str(e))
        return
    memory_writer = MemoryWriter(
        memory_writer_client,
        memory_writer_system_prompt,
        memory_writer_parse_retry_prompt,
        parse_retries=memory_writer_config.writer_parse_retries,
        max_retries=memory_writer_config.writer_max_retries,
        commit_log=SessionCommitLog(),
    )

    timezone = workspace.get_timezone()
    chat_input = ChatInput(timezone=timezone)
    conversation = Conversation()
    builder = ContextBuilder(system_prompt=system_prompt, timezone=timezone)
    registry = setup_tools(
        config.tools,
        working_dir,
        memory_writer=memory_writer,
    )
    commands = CommandHandler(console)

    # Optional reviewers
    pre_reviewer = None
    pre_warn_on_failure = True
    if "pre_reviewer" in config.agents:
        pre_config = config.agents["pre_reviewer"]
        pre_warn_on_failure = global_warn_on_failure and pre_config.warn_on_failure
        pre_client = create_client(
            pre_config.llm,
            timeout_retries=pre_config.llm_timeout_retries,
            request_timeout=pre_config.llm_request_timeout,
        )
        try:
            pre_prompt = workspace.get_system_prompt(
                "pre_reviewer", current_user=user_id
            )
            pre_parse_retry_prompt: str | None = None
            try:
                pre_parse_retry_prompt = workspace.get_agent_prompt(
                    "pre_reviewer",
                    "parse-retry",
                    current_user=user_id,
                )
            except FileNotFoundError:
                pass
            pre_reviewer = PreReviewer(
                pre_client,
                pre_prompt,
                registry,
                pre_config,
                parse_retry_prompt=pre_parse_retry_prompt,
            )
        except FileNotFoundError:
            pass

    post_reviewer = None
    post_max_retries = 2
    post_warn_on_failure = True
    if "post_reviewer" in config.agents:
        post_config = config.agents["post_reviewer"]
        post_max_retries = post_config.max_post_retries
        post_warn_on_failure = global_warn_on_failure and post_config.warn_on_failure
        post_client = create_client(
            post_config.llm,
            timeout_retries=post_config.llm_timeout_retries,
            request_timeout=post_config.llm_request_timeout,
        )
        try:
            post_prompt = workspace.get_system_prompt(
                "post_reviewer", current_user=user_id
            )
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
    shutdown_reviewer_warn_on_failure = True
    if "shutdown_reviewer" in config.agents:
        shutdown_config = config.agents["shutdown_reviewer"]
        shutdown_reviewer_max_retries = shutdown_config.max_post_retries
        shutdown_reviewer_warn_on_failure = (
            global_warn_on_failure and shutdown_config.warn_on_failure
        )
        shutdown_client = create_client(
            shutdown_config.llm,
            timeout_retries=shutdown_config.llm_timeout_retries,
            request_timeout=shutdown_config.llm_request_timeout,
        )
        try:
            shutdown_prompt = workspace.get_system_prompt(
                "shutdown_reviewer", current_user=user_id
            )
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

    console.print_welcome()

    while True:
        user_input = chat_input.get_input()

        if user_input is None:
            _graceful_exit(
                client, conversation, builder, registry,
                console, workspace, user_id,
                shutdown_reviewer=shutdown_reviewer,
                shutdown_reviewer_max_retries=shutdown_reviewer_max_retries,
                shutdown_reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
            )
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if commands.is_command(user_input):
            result = commands.execute(user_input)
            if result == CommandResult.QUIT:
                _graceful_exit(
                    client, conversation, builder, registry,
                    console, workspace, user_id,
                    shutdown_reviewer=shutdown_reviewer,
                    shutdown_reviewer_max_retries=shutdown_reviewer_max_retries,
                    shutdown_reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
                )
                break
            elif result == CommandResult.CLEAR:
                conversation = Conversation()
            continue

        pre_turn_anchor = len(conversation.get_messages())
        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            tools = registry.get_definitions()

            # === Pre-fetch pass ===
            if pre_reviewer is not None:
                with console.spinner("Reviewing..."):
                    pre_result = pre_reviewer.review(messages)
                if pre_result is None and pre_warn_on_failure:
                    console.print_warning(
                        _build_reviewer_warning(
                            "Pre-review",
                            pre_reviewer.last_raw_response,
                        )
                    )
                if debug:
                    raw = pre_reviewer.last_raw_response or "(empty)"
                    console.print_debug("pre-review raw", raw[:300])
                    if pre_result:
                        rules = ", ".join(pre_result.triggered_rules) or "(none)"
                        console.print_debug("pre-review rules", rules)
                        for a in pre_result.prefetch:
                            console.print_debug("pre-review prefetch", f"{a.tool}: {a.arguments} ({a.reason})")
                        for r in pre_result.reminders:
                            console.print_debug("pre-review reminder", r)
                    else:
                        console.print_debug("pre-review", "parse failed, skipping")
                if pre_result is not None and (
                    pre_result.prefetch or pre_result.reminders
                ):
                    prefetch_results = pre_reviewer.execute_prefetch(pre_result)
                    if debug:
                        console.print_debug("pre-review", f"fetched {len(prefetch_results)} results")
                    messages = builder.build_with_review(
                        conversation, prefetch_results, pre_result.reminders
                    )

            # === Responder ===
            turn_anchor = len(conversation.get_messages())
            response = _run_responder(
                client, messages, tools,
                conversation, builder, registry, console,
            )
            final_content, used_fallback_content = _resolve_final_content(
                response.content,
                conversation.get_messages()[turn_anchor:],
            )

            # === Post-review pass ===
            if post_reviewer is not None:
                if final_content and not used_fallback_content:
                    conversation.add("assistant", final_content)
                retry_count = 0
                last_action_signature: tuple[str, ...] | None = None
                fail_closed = False
                while True:
                    review_messages = builder.build(conversation)
                    if debug:
                        from ..reviewer.flatten import flatten_for_review
                        flat = flatten_for_review(review_messages)
                        total_chars = sum(len(m.content or "") for m in flat)
                        console.print_debug("post-review input", f"{len(flat)} msgs, {total_chars} chars")
                        for idx, m in enumerate(flat):
                            preview = (m.content or "")[:100].replace("\n", "\\n")
                            console.print_debug(f"post-review msg[{idx}]", f"role={m.role} len={len(m.content or '')} | {preview}")
                    with console.spinner("Checking..."):
                        post_result = post_reviewer.review(review_messages)
                    if post_result is None and post_warn_on_failure:
                        console.print_warning(
                            _build_reviewer_warning(
                                "Post-review",
                                post_reviewer.last_raw_response,
                            )
                        )
                    if debug:
                        raw = post_reviewer.last_raw_response or "(empty)"
                        console.print_debug("post-review raw", raw[:300])
                        if post_result:
                            status = "PASS" if post_result.passed else "FAIL"
                            console.print_debug("post-review", status)
                            for v in post_result.violations:
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
                        else:
                            console.print_debug("post-review", "parse failed, skipping")

                    if post_result is None:
                        fail_closed = True
                        break

                    turn_messages = conversation.get_messages()[turn_anchor:]
                    turn_missing_memory_write = not _has_memory_write(turn_messages)
                    retry_instruction = ""
                    violations: list[str] = []
                    actions_for_retry = _collect_required_actions_for_retry(
                        turn_messages,
                        passed=post_result.passed,
                        required_actions=post_result.required_actions,
                    )
                    missing_actions = _find_missing_actions(
                        turn_messages,
                        post_result.required_actions,
                    )

                    if post_result.passed:
                        if actions_for_retry and not retry_instruction:
                            retry_instruction = (
                                "Complete all required_actions before final answer."
                            )
                    else:
                        violations = post_result.violations
                        if post_result.required_actions and not missing_actions:
                            if debug:
                                console.print_debug(
                                    "post-review",
                                    "required actions already satisfied in this attempt; accepting",
                                )

                        retry_instruction = (
                            post_result.retry_instruction
                            or (post_result.guidance or "")
                        )

                    identity_augmented_actions = _ensure_identity_sync_action(
                        actions_for_retry,
                        turn_messages,
                    )
                    if (
                        len(identity_augmented_actions) > len(actions_for_retry)
                        and not retry_instruction
                    ):
                        retry_instruction = (
                            "Sync identity changes to memory/agent/persona.md "
                            "before final answer."
                        )
                    actions_for_retry = identity_augmented_actions

                    if turn_missing_memory_write:
                        actions_for_retry = _ensure_turn_persistence_action(actions_for_retry)
                        if not retry_instruction:
                            retry_instruction = (
                                "Persist this turn to memory before final answer."
                            )

                    if not actions_for_retry:
                        break

                    signature = _action_signature(actions_for_retry, violations)
                    if signature and signature == last_action_signature:
                        if post_warn_on_failure:
                            console.print_warning(
                                "Post-review detected repeated unresolved actions; stop retrying."
                            )
                        if debug:
                            console.print_debug(
                                "post-review",
                                "same action signature repeated, stop retries",
                            )
                        fail_closed = True
                        break
                    last_action_signature = signature

                    if retry_count >= post_max_retries:
                        if post_warn_on_failure:
                            console.print_warning(
                                "Post-review found unresolved actions after max retries."
                            )
                        fail_closed = True
                        break

                    retry_count += 1
                    if debug:
                        console.print_debug("post-review", f"retry {retry_count}/{post_max_retries}")

                    # Keep review guidance out of user-visible dialogue. Also rollback
                    # failed assistant/tool messages so the next review checks only
                    # the latest attempt for this user turn.
                    conversation._messages = conversation._messages[:turn_anchor]
                    reminder_text = _build_retry_reminder(
                        retry_instruction=retry_instruction,
                        required_actions=actions_for_retry,
                    )
                    reminders = [reminder_text] if reminder_text else []
                    messages = builder.build_with_review(conversation, [], reminders)
                    response = _run_responder(
                        client, messages, tools,
                        conversation, builder, registry, console,
                    )
                    final_content, used_fallback_content = _resolve_final_content(
                        response.content,
                        conversation.get_messages()[turn_anchor:],
                    )
                    if final_content and not used_fallback_content:
                        conversation.add("assistant", final_content)
                if fail_closed:
                    conversation._messages = conversation._messages[:turn_anchor]
                    console.print_error(
                        "Post-review unresolved (fail-closed); no assistant reply was sent."
                    )
                    continue
                console.print_assistant(final_content)
            else:
                if final_content and not used_fallback_content:
                    conversation.add("assistant", final_content)
                console.print_assistant(final_content)

        except Exception as e:
            console.print_error(str(e))
            conversation._messages = conversation._messages[:pre_turn_anchor]
            continue
