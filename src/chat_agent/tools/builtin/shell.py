"""Shell execution tool."""

from collections.abc import Callable

from ...llm.schema import ToolDefinition, ToolParameter
from ..executor import ShellExecutor

EXECUTE_SHELL_DEFINITION = ToolDefinition(
    name="execute_shell",
    description="Execute a shell command and return the output. The working directory persists across calls.",
    parameters={
        "command": ToolParameter(
            type="string",
            description="The shell command to execute.",
        ),
        "timeout": ToolParameter(
            type="integer",
            description="Timeout in seconds. Clamped to at least the configured default; cannot lower it.",
        ),
    },
    required=["command"],
)


def create_execute_shell(executor: ShellExecutor) -> Callable[..., str]:
    """Create an execute_shell function bound to an executor.

    Args:
        executor: The ShellExecutor instance to use.

    Returns:
        A function that executes shell commands.
    """

    def execute_shell(command: str, timeout: int | None = None) -> str:
        """Execute a shell command."""
        return executor.execute(command, timeout)

    return execute_shell
