"""Security utilities for path validation and shell command guards."""

from functools import lru_cache
from pathlib import Path
import re


def is_path_allowed(path: str, allowed_paths: list[str], base_dir: Path) -> bool:
    """Check if a path is within allowed directories.

    Args:
        path: The path to check (absolute or relative).
        allowed_paths: List of allowed directory paths.
        base_dir: Base directory for resolving relative paths.

    Returns:
        True if path is allowed, False otherwise.
    """
    # Resolve the target path
    target = Path(path)
    if not target.is_absolute():
        target = base_dir / target
    target = target.resolve()

    # If no allowed paths specified, only allow within base_dir
    if not allowed_paths:
        try:
            target.relative_to(base_dir)
            return True
        except ValueError:
            return False

    # Check against each allowed path
    for allowed in allowed_paths:
        allowed_path = Path(allowed).expanduser().resolve()
        try:
            target.relative_to(allowed_path)
            return True
        except ValueError:
            continue

    return False


@lru_cache(maxsize=None)
def build_memory_shell_write_patterns(
    agent_os_dir: Path,
) -> tuple[re.Pattern[str], ...]:
    """Build shell patterns that indicate direct memory writes."""
    memory_abs = re.escape(str((agent_os_dir / "memory").resolve()))
    memory_rel = r"(?:\./)?(?:\.agent/)?memory/"
    memory_target = rf"(?:['\"])?(?:{memory_rel}|{memory_abs}/)"
    return (
        re.compile(rf">>?\s*{memory_target}"),
        re.compile(rf"\btee(?:\s+-a)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\bsed\s+-i(?:\S*)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\brm\s[^\n]*{memory_target}"),
        re.compile(rf"\bmv\s[^\n]*{memory_target}"),
    )


def is_memory_write_shell_command(command: str, *, agent_os_dir: Path) -> bool:
    """Check if a shell command writes directly under memory/."""
    return any(
        pattern.search(command) is not None
        for pattern in build_memory_shell_write_patterns(agent_os_dir)
    )
