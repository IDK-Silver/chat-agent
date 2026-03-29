"""Tool registry for managing and executing tools."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..llm.schema import ContentPart, ToolCall, ToolDefinition

# Raw content type returned by individual tool functions.
ToolContent = str | list[ContentPart]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured result from tool execution with explicit error flag."""

    content: ToolContent
    is_error: bool = False


class ToolRegistry:
    """Registry for tools that can be called by LLM."""

    def __init__(self):
        self._tools: dict[str, tuple[Callable[..., Any], ToolDefinition]] = {}
        self._side_effect_tools: frozenset[str] = frozenset()

    def register(
        self,
        name: str,
        func: Callable[..., ToolContent],
        definition: ToolDefinition,
    ) -> None:
        """Register a tool with its definition."""
        if definition.name != name:
            raise ValueError(f"Tool name mismatch: {name} != {definition.name}")
        self._tools[name] = (func, definition)

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        if tool_call.name not in self._tools:
            return ToolResult(f"Error: Unknown tool '{tool_call.name}'", is_error=True)

        func, _ = self._tools[tool_call.name]
        try:
            content = func(**tool_call.arguments)
            # Auto-detect error strings returned by tool functions.
            is_error = isinstance(content, str) and content.startswith("Error")
            return ToolResult(content, is_error=is_error)
        except Exception as e:
            return ToolResult(f"Error executing {tool_call.name}: {e}", is_error=True)

    def get_definitions(self) -> list[ToolDefinition]:
        """Get all registered tool definitions."""
        return [defn for _, defn in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def set_side_effect_tools(self, names: frozenset[str]) -> None:
        """Declare which registered tools have side effects.

        Side-effect tools (send_message, memory_edit, etc.) may be
        preempted by the responder when fresher inbound arrives.
        """
        self._side_effect_tools = names

    def add_side_effect_tools(self, names: frozenset[str]) -> None:
        """Add additional side-effect tool names to the existing set."""
        self._side_effect_tools = self._side_effect_tools | names

    def is_side_effect(self, name: str) -> bool:
        """Return True when *name* is marked as a side-effect tool."""
        return name in self._side_effect_tools
