from pathlib import Path

from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import ToolsConfig
from ..llm import create_client
from ..workspace import WorkspaceManager, WorkspaceInitializer, KERNEL_VERSION
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
        console.print_info(f"Upgrading kernel to v{KERNEL_VERSION}...")
        initializer.upgrade_kernel()
        console.print_info("Kernel upgraded successfully.")

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

    brain_config = config.agents["brain"].llm
    client = create_client(brain_config)

    chat_input = ChatInput()
    conversation = Conversation()
    builder = ContextBuilder(system_prompt=system_prompt)
    registry = setup_tools(config.tools, working_dir)
    commands = CommandHandler(console)

    console.print_welcome()

    while True:
        user_input = chat_input.get_input()

        if user_input is None:
            console.print_goodbye()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if commands.is_command(user_input):
            result = commands.execute(user_input)
            if result == CommandResult.QUIT:
                console.print_goodbye()
                break
            elif result == CommandResult.CLEAR:
                conversation = Conversation()
            continue

        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            tools = registry.get_definitions()

            with console.spinner():
                response = client.chat_with_tools(messages, tools)

            # Process tool calls in a loop until no more tool calls
            while response.has_tool_calls():
                # Record assistant message with tool calls
                conversation.add_assistant_with_tools(response.content, response.tool_calls)

                # Execute each tool call and record results
                for tool_call in response.tool_calls:
                    console.print_tool_call(tool_call)
                    with console.spinner("Executing..."):
                        result = registry.execute(tool_call)
                    console.print_tool_result(tool_call, result)
                    conversation.add_tool_result(tool_call.id, tool_call.name, result)

                # Continue conversation with tool results
                messages = builder.build(conversation)
                with console.spinner():
                    response = client.chat_with_tools(messages, tools)

            # Record final assistant response
            final_content = response.content or ""
            conversation.add("assistant", final_content)
            console.print_assistant(final_content)

        except Exception as e:
            console.print_error(str(e))
            conversation._messages.pop()  # Remove failed user message
            continue
