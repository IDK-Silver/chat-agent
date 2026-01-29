from ..llm.schema import ToolCall


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
        else:
            return f"{len(lines)} lines"
    elif name == "get_current_time":
        return result
    else:
        if len(result) > 70:
            return result[:67] + "..."
        return result
