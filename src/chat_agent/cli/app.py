from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import ToolsConfig
from ..llm import create_client
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


def setup_tools(tools_config: ToolsConfig) -> ToolRegistry:
    """Set up the tool registry with built-in tools."""
    registry = ToolRegistry()

    # Time tool
    registry.register("get_current_time", get_current_time, GET_CURRENT_TIME_DEFINITION)

    # Shell executor
    working_dir = tools_config.get_working_dir()
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

    # File tools
    allowed_paths = tools_config.allowed_paths
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


def main() -> None:
    """Main entry point for the CLI."""
    config = load_config()
    brain_config = config.agents["brain"].llm
    client = create_client(brain_config)

    console = ChatConsole()
    chat_input = ChatInput()
    conversation = Conversation()
    builder = ContextBuilder()
    registry = setup_tools(config.tools)
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
