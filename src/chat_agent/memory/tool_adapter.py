"""Memory edit tool adapter (v2 strict instruction contract)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ..llm.schema import ToolDefinition, ToolParameter
from .editor.schema import MemoryEditBatch


class _MemoryEditorLike(Protocol):
    def apply_batch(
        self,
        batch: MemoryEditBatch,
        *,
        allowed_paths: list[str],
        base_dir: Path,
    ): ...


_MEMORY_EDIT_REQUEST_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "A single memory edit instruction request.",
    "properties": {
        "request_id": {
            "type": "string",
            "description": "Unique request id inside this batch.",
        },
        "target_path": {
            "type": "string",
            "description": "Target memory file path (must start with memory/).",
        },
        "instruction": {
            "type": "string",
            "description": "Natural-language edit intent for memory_editor planner.",
        },
    },
    "required": ["request_id", "target_path", "instruction"],
    "additionalProperties": False,
}


MEMORY_EDIT_DEFINITION = ToolDefinition(
    name="memory_edit",
    description=(
        "Persist memory updates under memory/ using instruction requests. "
        "Required root keys: as_of, turn_id, requests. "
        "Each request must contain request_id, target_path, instruction. "
        "Index links are auto-managed: creating/deleting files automatically "
        "updates parent index.md. Only use index.md as target to update "
        "descriptions, not to add/remove links. "
        "Operations may partially fail. Always verify results before taking "
        "dependent actions (e.g. deleting source files)."
    ),
    parameters={
        "as_of": ToolParameter(
            type="string",
            description="ISO timestamp string of this operation batch.",
        ),
        "turn_id": ToolParameter(
            type="string",
            description="Unique id for this conversation turn.",
        ),
        "requests": ToolParameter(
            type="array",
            description=(
                "List of instruction requests (max 12). "
                "Each request must include request_id, target_path, instruction."
            ),
            json_schema={
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": _MEMORY_EDIT_REQUEST_ITEM_SCHEMA,
            },
        ),
    },
    required=["as_of", "turn_id", "requests"],
)


def create_memory_edit(
    memory_editor: _MemoryEditorLike,
    *,
    allowed_paths: list[str],
    base_dir: Path,
) -> Callable[..., str]:
    """Create memory_edit tool function bound to memory editor service."""

    def memory_edit(
        as_of: str | None = None,
        turn_id: str | None = None,
        requests: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        """Apply instruction-style memory edit requests through memory editor."""
        if kwargs:
            extras = ", ".join(sorted(kwargs.keys()))
            return (
                "Error: Invalid memory_edit arguments: "
                f"unexpected keys: {extras}"
            )

        try:
            batch = MemoryEditBatch.model_validate(
                {
                    "as_of": as_of,
                    "turn_id": turn_id,
                    "requests": requests,
                }
            )
        except ValidationError as e:
            return f"Error: Invalid memory_edit arguments: {e}"

        result = memory_editor.apply_batch(
            batch,
            allowed_paths=allowed_paths,
            base_dir=base_dir,
        )
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    return memory_edit

