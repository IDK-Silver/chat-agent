"""Classification helpers for memory ``index.md`` files."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath


class IndexKind(str, Enum):
    """Semantic type of a memory index file."""

    NAV = "nav"
    REGISTRY = "registry"


_REGISTRY_INDEX_PATHS: dict[PurePosixPath, IndexKind] = {
    PurePosixPath("memory/people/index.md"): IndexKind.REGISTRY,
}


def classify_memory_index_path(path: str) -> IndexKind | None:
    """Return the semantic index kind for a memory-relative path."""
    normalized = PurePosixPath(str(path).strip().replace("\\", "/"))
    if normalized.name != "index.md":
        return None
    return _REGISTRY_INDEX_PATHS.get(normalized, IndexKind.NAV)


def is_registry_index_path(path: str) -> bool:
    """True when ``path`` is a registry-style memory index."""
    return classify_memory_index_path(path) == IndexKind.REGISTRY
