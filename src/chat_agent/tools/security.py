"""Security utilities for path validation."""

from pathlib import Path


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
