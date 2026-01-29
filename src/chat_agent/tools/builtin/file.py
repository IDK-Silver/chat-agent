"""File operation tools."""

from collections.abc import Callable
from pathlib import Path

from ...llm.schema import ToolDefinition, ToolParameter
from ..security import is_path_allowed

# Tool definitions
READ_FILE_DEFINITION = ToolDefinition(
    name="read_file",
    description="Read the contents of a file. Returns the file content with line numbers.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The path to the file to read.",
        ),
        "offset": ToolParameter(
            type="integer",
            description="Line number to start reading from (1-indexed). Defaults to 1.",
        ),
        "limit": ToolParameter(
            type="integer",
            description="Maximum number of lines to read. Defaults to 2000.",
        ),
    },
    required=["path"],
)

WRITE_FILE_DEFINITION = ToolDefinition(
    name="write_file",
    description="Write content to a file. Creates parent directories if needed. Overwrites existing content.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The path to the file to write.",
        ),
        "content": ToolParameter(
            type="string",
            description="The content to write to the file.",
        ),
    },
    required=["path", "content"],
)

EDIT_FILE_DEFINITION = ToolDefinition(
    name="edit_file",
    description="Edit a file by replacing a specific string. The old_string must be unique in the file unless replace_all is True.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The path to the file to edit.",
        ),
        "old_string": ToolParameter(
            type="string",
            description="The exact string to find and replace.",
        ),
        "new_string": ToolParameter(
            type="string",
            description="The string to replace with.",
        ),
        "replace_all": ToolParameter(
            type="boolean",
            description="If True, replace all occurrences. If False (default), the old_string must be unique.",
        ),
    },
    required=["path", "old_string", "new_string"],
)


def create_read_file(
    allowed_paths: list[str],
    base_dir: Path,
) -> Callable[..., str]:
    """Create a read_file function with path checking.

    Args:
        allowed_paths: List of allowed directory paths.
        base_dir: Base directory for path resolution.

    Returns:
        A function that reads files.
    """

    def read_file(
        path: str,
        offset: int = 1,
        limit: int = 2000,
    ) -> str:
        """Read a file with optional offset and limit."""
        if not is_path_allowed(path, allowed_paths, base_dir):
            return f"Error: Path '{path}' is not allowed"

        target = Path(path)
        if not target.is_absolute():
            target = base_dir / target

        if not target.exists():
            return f"Error: File '{target}' does not exist"

        if not target.is_file():
            return f"Error: '{target}' is not a file"

        try:
            content = target.read_bytes()
            # Check for binary content
            if b"\x00" in content[:8192]:
                return f"Error: '{target}' appears to be a binary file"

            lines = content.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return f"Error: '{target}' is not a valid UTF-8 file"
        except Exception as e:
            return f"Error reading file: {e}"

        # Apply offset and limit
        start = max(0, offset - 1)  # Convert 1-indexed to 0-indexed
        end = start + limit
        selected = lines[start:end]

        # Format with line numbers
        result = []
        for i, line in enumerate(selected, start=start + 1):
            result.append(f"{i:6d}\t{line}")

        return "\n".join(result)

    return read_file


def create_write_file(
    allowed_paths: list[str],
    base_dir: Path,
) -> Callable[..., str]:
    """Create a write_file function with path checking.

    Args:
        allowed_paths: List of allowed directory paths.
        base_dir: Base directory for path resolution.

    Returns:
        A function that writes files.
    """

    def write_file(path: str, content: str) -> str:
        """Write content to a file."""
        if not is_path_allowed(path, allowed_paths, base_dir):
            return f"Error: Path '{path}' is not allowed"

        target = Path(path)
        if not target.is_absolute():
            target = base_dir / target

        try:
            # Create parent directories if needed
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {target}"
        except Exception as e:
            return f"Error writing file: {e}"

    return write_file


def create_edit_file(
    allowed_paths: list[str],
    base_dir: Path,
) -> Callable[..., str]:
    """Create an edit_file function with path checking.

    Args:
        allowed_paths: List of allowed directory paths.
        base_dir: Base directory for path resolution.

    Returns:
        A function that edits files.
    """

    def edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """Edit a file by replacing strings."""
        if not is_path_allowed(path, allowed_paths, base_dir):
            return f"Error: Path '{path}' is not allowed"

        target = Path(path)
        if not target.is_absolute():
            target = base_dir / target

        if not target.exists():
            return f"Error: File '{target}' does not exist"

        if not target.is_file():
            return f"Error: '{target}' is not a file"

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        # Check for uniqueness
        count = content.count(old_string)
        if count == 0:
            return f"Error: '{old_string}' not found in file"

        if count > 1 and not replace_all:
            return f"Error: '{old_string}' appears {count} times. Use replace_all=True to replace all occurrences."

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            target.write_text(new_content, encoding="utf-8")
            return f"Successfully replaced {replaced} occurrence(s) in {target}"
        except Exception as e:
            return f"Error writing file: {e}"

    return edit_file
