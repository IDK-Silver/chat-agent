"""Memory edit tool backed by deterministic memory editor pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ..llm.schema import ToolDefinition, ToolParameter
from .editor.schema import MemoryEditBatch

_KIND_ALIASES = {
    "create": "create_if_missing",
    "create_if_missing": "create_if_missing",
    "create_if_not_exists": "create_if_missing",
    "create_if_absent": "create_if_missing",
    "append": "append_entry",
    "append_entry": "append_entry",
    "append_text": "append_entry",
    "append_line": "append_entry",
    "replace": "replace_block",
    "replace_block": "replace_block",
    "replace_text": "replace_block",
    "replace_line": "replace_block",
    "toggle": "toggle_checkbox",
    "toggle_checkbox": "toggle_checkbox",
    "check": "toggle_checkbox",
    "uncheck": "toggle_checkbox",
    "ensure_index_link": "ensure_index_link",
    "ensure_link": "ensure_index_link",
    "index_link": "ensure_index_link",
}
_CHECKBOX_LINE_RE = re.compile(r"^\s*-\s*\[(?P<state>[ xX])\]\s*(?P<text>.+?)\s*$")


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
    "description": "A single memory edit request.",
    "properties": {
        "request_id": {
            "type": "string",
            "description": "Unique request id inside this batch.",
        },
        "kind": {
            "type": "string",
            "description": "Operation kind.",
            "enum": [
                "create_if_missing",
                "append_entry",
                "replace_block",
                "toggle_checkbox",
                "ensure_index_link",
            ],
        },
        "target_path": {
            "type": "string",
            "description": "Target memory file path.",
        },
        "payload_text": {
            "type": "string",
            "description": "Required for create_if_missing and append_entry.",
        },
        "old_block": {
            "type": "string",
            "description": "Required for replace_block.",
        },
        "new_block": {
            "type": "string",
            "description": "Required for replace_block.",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Optional replace-all flag for replace_block.",
        },
        "item_text": {
            "type": "string",
            "description": "Required for toggle_checkbox.",
        },
        "checked": {
            "type": "boolean",
            "description": "Required for toggle_checkbox.",
        },
        "index_path": {
            "type": "string",
            "description": "Required for ensure_index_link.",
        },
        "link_path": {
            "type": "string",
            "description": "Required for ensure_index_link.",
        },
        "link_title": {
            "type": "string",
            "description": "Required for ensure_index_link.",
        },
        "section_hint": {
            "type": "string",
            "description": "Optional section hint.",
        },
    },
    "required": ["request_id", "kind", "target_path"],
}


MEMORY_EDIT_DEFINITION = ToolDefinition(
    name="memory_edit",
    description=(
        "Persist memory updates under memory/ using structured requests. "
        "Required root keys: as_of, turn_id, requests. "
        "Required request keys: request_id, kind, target_path (+ kind-specific fields). "
        "Minimal example: "
        "{\"as_of\":\"2026-02-09T01:10:00+08:00\",\"turn_id\":\"turn-123\","
        "\"requests\":[{\"request_id\":\"r1\",\"kind\":\"append_entry\","
        "\"target_path\":\"memory/short-term.md\",\"payload_text\":\"- [time] note\"}]}. "
        "Only accepts memory paths and returns per-request apply status."
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
                "List of structured memory edit requests (max 12). "
                "Each request must include request_id, kind, target_path, and required fields "
                "for that kind: create_if_missing/append_entry->payload_text; "
                "replace_block->old_block+new_block(+replace_all optional); "
                "toggle_checkbox->item_text+checked; "
                "ensure_index_link->index_path+link_path+link_title."
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
    """Create memory_edit tool function bound to writer service."""

    def memory_edit(
        as_of: str | None = None,
        turn_id: str | None = None,
        requests: list[dict[str, Any]] | str | None = None,
        **kwargs: Any,
    ) -> str:
        """Apply structured memory edit requests through dedicated writer."""
        as_of_value = as_of or _pick_string(kwargs, "timestamp", "asOf")
        turn_id_value = turn_id or _pick_string(
            kwargs,
            "turn",
            "turnId",
            "conversation_turn_id",
        )
        requests_value: list[dict[str, Any]] | str | None = requests
        if requests_value is None:
            updates = kwargs.get("updates")
            operations = kwargs.get("operations")
            ops = kwargs.get("ops")
            requests_value = (
                updates if updates is not None
                else operations if operations is not None
                else ops
            )
        normalized_requests = _normalize_requests(requests_value)

        try:
            batch = MemoryEditBatch.model_validate(
                {
                    "as_of": as_of_value,
                    "turn_id": turn_id_value,
                    "requests": normalized_requests,
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


def _pick_string(source: dict[str, Any], *keys: str) -> str | None:
    """Pick first non-empty string from source by candidate keys."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _coerce_bool(value: Any) -> bool | Any:
    """Best-effort boolean coercion for compatibility payloads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "checked", "done"}:
            return True
        if normalized in {"false", "0", "no", "n", "unchecked", "todo"}:
            return False
    return value


def _pick_first(raw: dict[str, Any], *keys: str) -> Any:
    """Pick first present key from raw mapping."""
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _normalize_requests(value: Any) -> Any:
    """Normalize compatibility request payloads to v1 memory_edit schema."""
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return value

    if isinstance(parsed, dict):
        wrapped = _pick_first(parsed, "requests", "updates", "operations", "ops")
        if wrapped is not None:
            parsed = wrapped

    if not isinstance(parsed, list):
        return parsed

    normalized_requests: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            normalized_requests.append(item)  # type: ignore[arg-type]
            continue

        kind = _pick_first(item, "kind", "action", "op", "type")
        kind_text = kind.strip().lower() if isinstance(kind, str) else ""
        request_id = _pick_first(
            item,
            "request_id",
            "requestId",
            "id",
            "op_id",
            "opId",
        )
        target_path = _pick_first(
            item,
            "target_path",
            "targetPath",
            "path",
            "target",
            "file_path",
            "filePath",
            "file",
        )
        payload_text = _pick_first(
            item,
            "payload_text",
            "payloadText",
            "content",
            "payload",
            "text",
            "entry",
        )
        item_text = _pick_first(
            item,
            "item_text",
            "itemText",
            "item",
            "label",
            "task",
            "checkbox_text",
        )
        old_block = _pick_first(
            item,
            "old_block",
            "oldBlock",
            "old_string",
            "oldString",
            "from",
            "before",
        )
        new_block = _pick_first(
            item,
            "new_block",
            "newBlock",
            "new_string",
            "newString",
            "to",
            "after",
        )
        replace_all = _coerce_bool(_pick_first(item, "replace_all", "replaceAll", "all"))
        checked = _coerce_bool(_pick_first(item, "checked", "is_checked", "done", "value"))
        index_path = _pick_first(item, "index_path", "indexPath")
        link_path = _pick_first(item, "link_path", "linkPath")
        link_title = _pick_first(item, "link_title", "linkTitle", "title")
        section_hint = _pick_first(item, "section_hint", "section")

        normalized: dict[str, Any] = dict(item)
        normalized_kind = _normalize_kind(
            kind,
            item,
            payload_text,
            old_block,
            new_block,
            item_text,
            checked,
            index_path,
            link_path,
            link_title,
        )
        if isinstance(normalized_kind, str):
            normalized["kind"] = normalized_kind
        if isinstance(request_id, str):
            normalized["request_id"] = request_id
        elif "request_id" not in normalized:
            normalized["request_id"] = f"auto-{idx + 1}"
        if isinstance(target_path, str):
            normalized["target_path"] = target_path
        if isinstance(payload_text, str):
            normalized["payload_text"] = payload_text
        if isinstance(item_text, str):
            normalized["item_text"] = item_text
        if isinstance(old_block, str):
            normalized["old_block"] = old_block
        if isinstance(new_block, str):
            normalized["new_block"] = new_block
        if isinstance(replace_all, bool):
            normalized["replace_all"] = replace_all
        if isinstance(checked, bool):
            normalized["checked"] = checked
        if isinstance(index_path, str):
            normalized["index_path"] = index_path
        if isinstance(link_path, str):
            normalized["link_path"] = link_path
        if isinstance(link_title, str):
            normalized["link_title"] = link_title
        if isinstance(section_hint, str):
            normalized["section_hint"] = section_hint

        if normalized.get("kind") == "toggle_checkbox":
            inferred_item_text, inferred_checked = _infer_toggle_checkbox_fields(
                kind_text=kind_text,
                payload_text=payload_text,
                old_block=old_block,
                new_block=new_block,
            )
            if "item_text" not in normalized and inferred_item_text is not None:
                normalized["item_text"] = inferred_item_text
            if "checked" not in normalized and inferred_checked is not None:
                normalized["checked"] = inferred_checked

            # Last-resort compatibility fallback:
            # if model emits toggle_checkbox without required fields but has replacement payload,
            # degrade to deterministic kind that can still be validated/applied.
            if (
                not isinstance(normalized.get("item_text"), str)
                or not isinstance(normalized.get("checked"), bool)
            ):
                if isinstance(normalized.get("old_block"), str) and isinstance(
                    normalized.get("new_block"), str
                ):
                    normalized["kind"] = "replace_block"
                elif isinstance(normalized.get("payload_text"), str):
                    normalized["kind"] = "append_entry"

        if normalized.get("kind") == "ensure_index_link":
            if (
                "index_path" not in normalized
                and isinstance(normalized.get("target_path"), str)
            ):
                normalized["index_path"] = normalized["target_path"]
            if (
                "target_path" not in normalized
                and isinstance(normalized.get("index_path"), str)
            ):
                # Compatibility: some models only emit index_path for ensure_index_link.
                normalized["target_path"] = normalized["index_path"]

        normalized_requests.append(normalized)

    return normalized_requests


def _parse_checkbox_line(text: Any) -> tuple[str | None, bool | None]:
    """Parse '- [ ] text' or '- [x] text' line into item_text + checked."""
    if not isinstance(text, str):
        return None, None
    match = _CHECKBOX_LINE_RE.match(text.strip())
    if match is None:
        return None, None
    checked = match.group("state").lower() == "x"
    item_text = match.group("text").strip()
    if not item_text:
        return None, checked
    return item_text, checked


def _infer_toggle_checkbox_fields(
    *,
    kind_text: str,
    payload_text: Any,
    old_block: Any,
    new_block: Any,
) -> tuple[str | None, bool | None]:
    """Infer toggle fields from common compatibility payloads."""
    if kind_text == "check":
        item_text, _ = _parse_checkbox_line(payload_text)
        return item_text, True
    if kind_text == "uncheck":
        item_text, _ = _parse_checkbox_line(payload_text)
        return item_text, False

    item_text, checked = _parse_checkbox_line(payload_text)
    if item_text is not None or checked is not None:
        return item_text, checked

    # Some models emit old/new checkbox lines for a toggle operation.
    new_item, new_checked = _parse_checkbox_line(new_block)
    old_item, _ = _parse_checkbox_line(old_block)
    if new_item is not None or old_item is not None or new_checked is not None:
        return new_item or old_item, new_checked

    return None, None


def _normalize_kind(
    raw_kind: Any,
    raw_item: dict[str, Any],
    payload_text: Any,
    old_block: Any,
    new_block: Any,
    item_text: Any,
    checked: Any,
    index_path: Any,
    link_path: Any,
    link_title: Any,
) -> str | None:
    """Map kind aliases and infer missing operation kinds."""
    if isinstance(raw_kind, str):
        normalized = _KIND_ALIASES.get(raw_kind.strip().lower())
        if normalized is not None:
            return normalized

    if isinstance(link_path, str) or isinstance(link_title, str) or isinstance(index_path, str):
        return "ensure_index_link"

    if isinstance(old_block, str) and isinstance(new_block, str):
        return "replace_block"

    if isinstance(item_text, str) and isinstance(checked, bool):
        return "toggle_checkbox"

    if isinstance(payload_text, str):
        create_flag = _pick_first(
            raw_item,
            "create_if_missing",
            "create",
            "if_missing",
            "create_if_not_exists",
        )
        if _coerce_bool(create_flag) is True:
            return "create_if_missing"
        return "append_entry"

    return None
