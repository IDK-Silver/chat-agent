"""Schemas for memory writer pipeline."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator


RequestKind = Literal[
    "create_if_missing",
    "append_entry",
    "toggle_checkbox",
    "ensure_index_link",
]


ApplyStatus = Literal["applied", "noop", "already_applied"]


class MemoryEditRequest(BaseModel):
    """Single memory edit request from brain."""

    request_id: str
    kind: RequestKind
    target_path: str
    payload_text: str | None = None
    item_text: str | None = None
    checked: bool | None = None
    index_path: str | None = None
    link_path: str | None = None
    link_title: str | None = None
    section_hint: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "MemoryEditRequest":
        if self.kind in {"create_if_missing", "append_entry"}:
            if self.payload_text is None:
                raise ValueError("payload_text is required for create_if_missing/append_entry")

        if self.kind == "toggle_checkbox":
            if self.item_text is None or self.checked is None:
                raise ValueError("item_text and checked are required for toggle_checkbox")

        if self.kind == "ensure_index_link":
            if self.index_path is None or self.link_path is None or self.link_title is None:
                raise ValueError(
                    "index_path, link_path, and link_title are required for ensure_index_link"
                )

        return self

    def semantic_payload(self) -> str:
        """Canonical payload used for semantic lock hash."""
        if self.payload_text is not None:
            return self.payload_text
        obj = {
            "kind": self.kind,
            "item_text": self.item_text,
            "checked": self.checked,
            "index_path": self.index_path,
            "link_path": self.link_path,
            "link_title": self.link_title,
            "section_hint": self.section_hint,
        }
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)

    def payload_hash(self) -> str:
        """Get SHA256 hash of semantic payload."""
        return hashlib.sha256(self.semantic_payload().encode("utf-8")).hexdigest()


class MemoryEditBatch(BaseModel):
    """Batch request for memory_edit tool."""

    as_of: str
    turn_id: str
    requests: list[MemoryEditRequest] = Field(min_length=1, max_length=12)


class WriterDecision(BaseModel):
    """Writer model output. It cannot carry free-form replacement text."""

    request_id: str
    kind: RequestKind
    target_path: str
    payload_hash: str
    decision: Literal["apply", "noop"]
    reason: str = ""


class AppliedItem(BaseModel):
    """Applied/noop/already_applied status for one request."""

    request_id: str
    status: ApplyStatus
    path: str


class ErrorItem(BaseModel):
    """Error details for one request."""

    request_id: str
    code: str
    detail: str


class MemoryEditResult(BaseModel):
    """Result payload for memory_edit tool."""

    status: Literal["ok", "failed"]
    turn_id: str
    applied: list[AppliedItem]
    errors: list[ErrorItem]
    writer_attempts: dict[str, int]

