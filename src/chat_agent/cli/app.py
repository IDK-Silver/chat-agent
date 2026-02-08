from pathlib import Path
from fnmatch import fnmatch

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import ToolsConfig
from ..llm import LLMResponse, create_client
from ..llm.base import LLMClient
from ..llm.schema import Message, ToolCall, ToolDefinition
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
    create_read_file,
    create_write_file,
    create_edit_file,
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
        if tool_call.name not in {"write_file", "edit_file"}:
            return False
    elif tool_call.name != action.tool:
        return False

    if action.tool in {"write_file", "edit_file", "write_or_edit", "read_file"}:
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
        tc.name in {"write_file", "edit_file"}
        and str(tc.arguments.get("path", "")) == action.index_path
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
        if tool_call.name not in {"write_file", "edit_file"}:
            continue
        path = str(tool_call.arguments.get("path", ""))
        if path.startswith("memory/"):
            return True
    return False


def _build_turn_persistence_action() -> RequiredAction:
    """Build fallback action to force minimum per-turn memory persistence."""
    return RequiredAction(
        code="persist_turn_memory",
        description=(
            "Persist this turn to rolling memory via memory/short-term.md "
            "before finalizing the user-facing answer."
        ),
        tool="write_or_edit",
        target_path="memory/short-term.md",
    )


def _ensure_turn_persistence_action(
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Append per-turn persistence action if not already covered."""
    for action in required_actions:
        if action.code == "persist_turn_memory":
            return required_actions
        if action.tool in {"write_file", "edit_file", "write_or_edit"}:
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


def setup_tools(tools_config: ToolsConfig, working_dir: Path) -> ToolRegistry:
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
    registry.register(
        "execute_shell",
        create_execute_shell(executor),
        EXECUTE_SHELL_DEFINITION,
    )

    # File tools - allow access to working_dir
    allowed_paths = list(tools_config.allowed_paths)
    # Always allow working_dir for memory access
    allowed_paths.insert(0, str(working_dir))

    registry.register(
        "read_file",
        create_read_file(allowed_paths, working_dir),
        READ_FILE_DEFINITION,
    )
    registry.register(
        "write_file",
        create_write_file(allowed_paths, working_dir),
        WRITE_FILE_DEFINITION,
    )
    registry.register(
        "edit_file",
        create_edit_file(allowed_paths, working_dir),
        EDIT_FILE_DEFINITION,
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
        console.print_assistant(response.content)
        conversation.add_assistant_with_tools(response.content, response.tool_calls)

        for tool_call in response.tool_calls:
            console.print_tool_call(tool_call)
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)

        messages = builder.build(conversation)
        with console.spinner():
            response = client.chat_with_tools(messages, tools)

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
            perform_shutdown(
                client, conversation, builder, registry,
                console, workspace, user_id,
                reviewer=shutdown_reviewer,
                reviewer_max_retries=shutdown_reviewer_max_retries,
                reviewer_warn_on_failure=shutdown_reviewer_warn_on_failure,
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
    global_warn_on_failure = config.warn_on_failure

    brain_agent_config = config.agents["brain"]
    client = create_client(
        brain_agent_config.llm,
        timeout_retries=brain_agent_config.llm_timeout_retries,
        request_timeout=brain_agent_config.llm_request_timeout,
    )

    timezone = workspace.get_timezone()
    chat_input = ChatInput(timezone=timezone)
    conversation = Conversation()
    builder = ContextBuilder(system_prompt=system_prompt, timezone=timezone)
    registry = setup_tools(config.tools, working_dir)
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
            final_content = response.content or ""

            # === Post-review pass ===
            if post_reviewer is not None:
                conversation.add("assistant", final_content)
                retry_count = 0
                last_action_signature: tuple[str, ...] | None = None
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
                    turn_messages = conversation.get_messages()[turn_anchor:]
                    turn_missing_memory_write = not _has_memory_write(turn_messages)
                    actions_for_retry: list[RequiredAction] = []
                    retry_instruction = ""
                    violations: list[str] = []

                    if post_result is None:
                        violations = ["post_review_unavailable"]
                        if turn_missing_memory_write:
                            actions_for_retry = [_build_turn_persistence_action()]
                            retry_instruction = (
                                "Post-review unavailable. Persist this turn to memory before "
                                "final answer."
                            )
                    elif post_result.passed:
                        if turn_missing_memory_write:
                            actions_for_retry = [_build_turn_persistence_action()]
                            retry_instruction = (
                                "Persist this turn to memory before final answer."
                            )
                    else:
                        violations = post_result.violations
                        missing_actions = _find_missing_actions(
                            turn_messages, post_result.required_actions
                        )
                        if post_result.required_actions and not missing_actions:
                            if debug:
                                console.print_debug(
                                    "post-review",
                                    "required actions already satisfied in this attempt; accepting",
                                )
                        else:
                            actions_for_retry = missing_actions or post_result.required_actions

                        retry_instruction = (
                            post_result.retry_instruction
                            or (post_result.guidance or "")
                        )

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
                        break
                    last_action_signature = signature

                    if retry_count >= post_max_retries:
                        if post_warn_on_failure:
                            console.print_warning(
                                "Post-review found unresolved actions after max retries."
                            )
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
                    final_content = response.content or ""
                    conversation.add("assistant", final_content)
                console.print_assistant(final_content)
            else:
                conversation.add("assistant", final_content)
                console.print_assistant(final_content)

        except Exception as e:
            console.print_error(str(e))
            conversation._messages.pop()  # Remove failed user message
            continue
