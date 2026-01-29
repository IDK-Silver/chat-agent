from .context import ContextBuilder, Conversation
from .core import load_config
from .core.schema import ToolsConfig
from .llm import create_client
from .llm.schema import ToolCall
from .tools import (
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


def format_tool_call(tool_call: ToolCall) -> str:
    """Format tool call for display."""
    name = tool_call.name
    args = tool_call.arguments

    if name == "read_file":
        return f"Read: {args.get('path', '?')}"
    elif name == "write_file":
        return f"Write: {args.get('path', '?')}"
    elif name == "edit_file":
        return f"Edit: {args.get('path', '?')}"
    elif name == "execute_shell":
        cmd = args.get("command", "?")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"Shell: {cmd}"
    elif name == "get_current_time":
        tz = args.get("timezone", "UTC")
        return f"Time: {tz}"
    else:
        return f"{name}: {args}"


def format_tool_result(tool_call: ToolCall, result: str) -> str:
    """Format tool result for display."""
    name = tool_call.name

    if result.startswith("Error"):
        # Show first line of error
        first_line = result.split("\n")[0]
        if len(first_line) > 70:
            first_line = first_line[:67] + "..."
        return first_line

    if name == "read_file":
        lines = result.count("\n") + 1 if result else 0
        return f"{lines} lines"
    elif name == "write_file":
        return result.split("\n")[0]  # "Successfully wrote X bytes..."
    elif name == "edit_file":
        return result.split("\n")[0]  # "Successfully replaced..."
    elif name == "execute_shell":
        lines = result.strip().split("\n")
        if len(lines) == 1 and len(lines[0]) <= 70:
            return lines[0] if lines[0] else "(empty)"
        elif len(lines) > 3:
            return f"{len(lines)} lines"
        else:
            return f"{len(lines)} lines"
    elif name == "get_current_time":
        return result
    else:
        if len(result) > 70:
            return result[:67] + "..."
        return result


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


def main():
    config = load_config()
    brain_config = config.agents["brain"].llm
    client = create_client(brain_config)

    conversation = Conversation()
    builder = ContextBuilder()
    registry = setup_tools(config.tools)

    print("Chat started. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("Bye!")
            break

        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            tools = registry.get_definitions()
            response = client.chat_with_tools(messages, tools)

            # Process tool calls in a loop until no more tool calls
            while response.has_tool_calls():
                # Record assistant message with tool calls
                conversation.add_assistant_with_tools(response.content, response.tool_calls)

                # Execute each tool call and record results
                for tool_call in response.tool_calls:
                    print(f"  {format_tool_call(tool_call)}")
                    result = registry.execute(tool_call)
                    print(f"    {format_tool_result(tool_call, result)}")
                    conversation.add_tool_result(tool_call.id, tool_call.name, result)

                # Continue conversation with tool results
                messages = builder.build(conversation)
                response = client.chat_with_tools(messages, tools)

            # Record final assistant response
            final_content = response.content or ""
            conversation.add("assistant", final_content)
            print(f"Assistant: {final_content}\n")

        except Exception as e:
            print(f"Error: {e}")
            conversation._messages.pop()  # Remove failed user message
            continue


if __name__ == "__main__":
    main()
