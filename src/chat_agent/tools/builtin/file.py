"""File operation tools."""

import json
from difflib import SequenceMatcher
from collections.abc import Callable
from pathlib import Path

from ...llm.schema import ToolDefinition, ToolParameter
from ..security import is_path_allowed

# Tool definitions
READ_FILE_DEFINITION = ToolDefinition(
    name="read_file",
    description=(
        "Read file content. By default returns text with line numbers. "
        "Set output_format='json' for structured output with metadata."
    ),
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
        "output_format": ToolParameter(
            type="string",
            description="Output format: 'text' (default) or 'json'.",
            enum=["text", "json"],
        ),
    },
    required=["path"],
)

WRITE_FILE_DEFINITION = ToolDefinition(
    name="write_file",
    description="Create a file or write to an existing empty file. Fails if target file already has content.",
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
        output_format: str = "text",
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

        if output_format not in {"text", "json"}:
            return "Error: Invalid output_format. Use 'text' or 'json'."

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

        if output_format == "json":
            payload = {
                "path": path,
                "resolved_path": str(target),
                "encoding": "utf-8",
                "offset": offset,
                "limit": limit,
                "total_lines": len(lines),
                "returned_lines": len(selected),
                "start_line": start + 1,
                "end_line": start + len(selected),
                "truncated": end < len(lines),
                "lines": [
                    {"line": i, "content": line}
                    for i, line in enumerate(selected, start=start + 1)
                ],
            }
            return json.dumps(payload, ensure_ascii=False)

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

        if target.exists() and not target.is_file():
            return f"Error: '{target}' is not a file"

        try:
            # Create parent directories if needed
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists() and target.stat().st_size > 0:
                return (
                    f"Error: Refusing to overwrite non-empty file '{target}'. "
                    "Use edit_file for updates."
                )

            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {target}"
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
            return _build_not_found_error(old_string, content)

        if count > 1 and not replace_all:
            lines = _find_occurrence_lines(content, old_string)
            line_hint = ""
            if lines:
                preview = ", ".join(str(n) for n in lines[:5])
                line_hint = f" First matches at lines: {preview}."
            return (
                f"Error: '{_preview_text(old_string)}' appears {count} times."
                f"{line_hint} Use replace_all=True to replace all occurrences."
            )

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


def _preview_text(text: str, max_len: int = 80) -> str:
    """Create a compact preview for error messages."""
    compact = text.replace("\n", "\\n")
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def _normalize_text(text: str) -> str:
    """Normalize line endings and trailing spaces for fuzzy comparison."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def _find_occurrence_lines(content: str, needle: str) -> list[int]:
    """Find 1-indexed line numbers where an exact needle occurs."""
    if not needle:
        return []

    positions: list[int] = []
    cursor = 0
    while True:
        idx = content.find(needle, cursor)
        if idx < 0:
            break
        positions.append(content.count("\n", 0, idx) + 1)
        cursor = idx + 1
    return positions


def _find_similar_lines(content: str, needle: str, max_items: int = 3) -> list[str]:
    """Return best-effort similar lines with line numbers."""
    query = needle.strip()
    if not query:
        return []

    candidates: list[tuple[float, int, str]] = []
    for idx, line in enumerate(content.splitlines(), start=1):
        ratio = SequenceMatcher(None, query, line.strip()).ratio()
        if ratio >= 0.45:
            candidates.append((ratio, idx, line))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [f"{idx}: {line}" for _, idx, line in candidates[:max_items]]


def _build_not_found_error(old_string: str, content: str) -> str:
    """Build an actionable not-found message for edit failures."""
    hints: list[str] = []

    normalized_count = _normalize_text(content).count(_normalize_text(old_string))
    if normalized_count > 0:
        hints.append(
            f"Found {normalized_count} match(es) after normalizing line endings/trailing spaces."
        )

    stripped = old_string.strip()
    if stripped and stripped != old_string:
        stripped_count = content.count(stripped)
        if stripped_count > 0:
            hints.append(
                f"Found {stripped_count} match(es) after stripping surrounding whitespace."
            )

    similar = _find_similar_lines(content, old_string.splitlines()[0] if old_string else "")
    if similar:
        hints.append("Similar lines: " + " | ".join(similar))

    hint_text = " Hint: " + " ".join(hints) if hints else " Hint: Use read_file to copy exact text."
    return f"Error: '{_preview_text(old_string)}' not found in file.{hint_text}"
