"""Shell execution tool."""

from collections.abc import Callable
import re
import shlex

from ...llm.schema import ToolDefinition, ToolParameter
from ..executor import ShellExecutor

EXECUTE_SHELL_DEFINITION = ToolDefinition(
    name="execute_shell",
    description="Execute a non-interactive shell command and return the output. The working directory persists across calls. Stdin is closed.",
    parameters={
        "command": ToolParameter(
            type="string",
            description="The non-interactive shell command to execute.",
        ),
        "timeout": ToolParameter(
            type="integer",
            description="Timeout in seconds. Clamped to at least the configured default; cannot lower it.",
        ),
    },
    required=["command"],
)

_STREAM_JSON_FLAG_RE = re.compile(r"--output-format(?:=|\s+)stream-json(?:\s|$)")
_CLAUDE_SEGMENT_RE = re.compile(r"(?:^|&&|\|\||;|\n)\s*(?:env\s+)?claude(?:\s|$)")
_ENV_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def _is_env_assignment_token(token: str) -> bool:
    return bool(_ENV_ASSIGNMENT_RE.fullmatch(token))


def is_claude_code_stream_json_command(command: str) -> bool:
    """Return True when the shell command runs Claude Code stream-json mode.

    This is intentionally conservative to avoid enabling stream-json
    transforms for unrelated commands that merely contain the flag text.
    """
    if not isinstance(command, str):
        return False
    if "stream-json" not in command or "--output-format" not in command:
        return False

    try:
        tokens = shlex.split(command, posix=True, comments=True)
    except ValueError:
        tokens = None

    if tokens:
        operators = {"&&", "||", ";", "|", "&"}
        has_claude_command = False
        for i, tok in enumerate(tokens):
            if tok != "claude" and not tok.endswith("/claude"):
                continue
            j = i - 1
            while j >= 0 and (tokens[j] == "env" or _is_env_assignment_token(tokens[j])):
                j -= 1
            if j >= 0 and tokens[j] not in operators:
                continue
            has_claude_command = True
            break
        if not has_claude_command:
            return False
        for i, tok in enumerate(tokens):
            if tok == "--output-format" and i + 1 < len(tokens) and tokens[i + 1] == "stream-json":
                return True
            if tok == "--output-format=stream-json":
                return True
        return False

    # Fallback for complex shell syntax (heredoc, multiline, etc.)
    return bool(_CLAUDE_SEGMENT_RE.search(command) and _STREAM_JSON_FLAG_RE.search(command))


def create_execute_shell(
    executor: ShellExecutor,
    on_stdout_line: Callable[[str], None] | None = None,
    output_transform: Callable[[list[str]], str | None] | None = None,
) -> Callable[..., str]:
    """Create an execute_shell function bound to an executor.

    Args:
        executor: The ShellExecutor instance to use.
        on_stdout_line: Optional callback forwarded to executor for
            real-time stdout streaming.
        output_transform: Optional function to convert collected stdout
            lines into the final result string (streaming mode only).

    Returns:
        A function that executes shell commands.
    """

    def execute_shell(command: str, timeout: int | None = None) -> str:
        """Execute a shell command."""
        # Only activate streaming + transform for stream-json commands;
        # normal commands must preserve their raw output.
        is_stream_json = (
            on_stdout_line is not None
            and is_claude_code_stream_json_command(command)
        )
        return executor.execute(
            command, timeout,
            on_stdout_line=on_stdout_line if is_stream_json else None,
            output_transform=output_transform if is_stream_json else None,
        )

    return execute_shell
