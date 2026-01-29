"""Tool registry for managing and executing tools."""

from collections.abc import Callable

from ..llm.schema import ToolCall, ToolDefinition


class ToolRegistry:
    """Registry for tools that can be called by LLM."""

    def __init__(self):
        self._tools: dict[str, tuple[Callable[..., str], ToolDefinition]] = {}

    def register(
        self,
        name: str,
        func: Callable[..., str],
        definition: ToolDefinition,
    ) -> None:
        """Register a tool with its definition."""
        if definition.name != name:
            raise ValueError(f"Tool name mismatch: {name} != {definition.name}")
        self._tools[name] = (func, definition)

    def execute(self, tool_call: ToolCall) -> str:
        """Execute a tool call and return the result."""
        if tool_call.name not in self._tools:
            return f"Error: Unknown tool '{tool_call.name}'"

        func, _ = self._tools[tool_call.name]
        try:
            return func(**tool_call.arguments)
        except Exception as e:
            return f"Error executing {tool_call.name}: {e}"

    def get_definitions(self) -> list[ToolDefinition]:
        """Get all registered tool definitions."""
        return [defn for _, defn in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
