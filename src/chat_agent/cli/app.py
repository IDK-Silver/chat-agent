from pathlib import Path

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import ToolsConfig
from ..llm import LLMResponse, create_client
from ..llm.base import LLMClient
from ..llm.schema import Message, ToolDefinition
from ..reviewer import PreReviewer, PostReviewer
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


def _graceful_exit(client, conversation, builder, registry, console, workspace, user_id):
    """Handle graceful exit with optional memory saving."""
    if _has_conversation_content(conversation):
        try:
            perform_shutdown(
                client, conversation, builder, registry,
                console, workspace, user_id,
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

    brain_config = config.agents["brain"].llm
    client = create_client(brain_config)

    timezone = workspace.get_timezone()
    chat_input = ChatInput(timezone=timezone)
    conversation = Conversation()
    builder = ContextBuilder(system_prompt=system_prompt, timezone=timezone)
    registry = setup_tools(config.tools, working_dir)
    commands = CommandHandler(console)

    # Optional reviewers
    pre_reviewer = None
    if "pre_reviewer" in config.agents:
        pre_config = config.agents["pre_reviewer"]
        pre_client = create_client(pre_config.llm)
        try:
            pre_prompt = workspace.get_agent_prompt(
                "brain", "reviewer-pre", current_user=user_id
            )
            pre_reviewer = PreReviewer(pre_client, pre_prompt, registry, pre_config)
        except FileNotFoundError:
            pass

    post_reviewer = None
    post_max_retries = 2
    if "post_reviewer" in config.agents:
        post_config = config.agents["post_reviewer"]
        post_max_retries = post_config.max_post_retries
        post_client = create_client(post_config.llm)
        try:
            post_prompt = workspace.get_agent_prompt(
                "brain", "reviewer-post", current_user=user_id
            )
            post_reviewer = PostReviewer(post_client, post_prompt)
        except FileNotFoundError:
            pass

    console.print_welcome()

    while True:
        user_input = chat_input.get_input()

        if user_input is None:
            _graceful_exit(
                client, conversation, builder, registry,
                console, workspace, user_id,
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
            response = _run_responder(
                client, messages, tools,
                conversation, builder, registry, console,
            )
            final_content = response.content or ""

            # === Post-review pass ===
            if post_reviewer is not None:
                conversation.add("assistant", final_content)
                retry_count = 0
                while retry_count < post_max_retries:
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
                    if debug:
                        raw = post_reviewer.last_raw_response or "(empty)"
                        console.print_debug("post-review raw", raw[:300])
                        if post_result:
                            status = "PASS" if post_result.passed else "FAIL"
                            console.print_debug("post-review", status)
                            for v in post_result.violations:
                                console.print_debug("post-review violation", v)
                            if post_result.guidance:
                                console.print_debug("post-review guidance", post_result.guidance)
                        else:
                            console.print_debug("post-review", "parse failed, skipping")
                    if post_result is None or post_result.passed:
                        break
                    retry_count += 1
                    if debug:
                        console.print_debug("post-review", f"retry {retry_count}/{post_max_retries}")
                    conversation.add("user", f"[System Review] {post_result.guidance}")
                    messages = builder.build(conversation)
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
