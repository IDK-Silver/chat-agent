from .context import ContextBuilder, Conversation
from .core import load_config
from .core.schema import ToolsConfig
from .llm import create_client
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
                    result = registry.execute(tool_call)
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
