"""Pydantic models for reviewer input/output."""

from typing import Literal

from pydantic import BaseModel


class PrefetchAction(BaseModel):
    """A single pre-fetch action to execute before the responder."""

    tool: Literal["read_file", "execute_shell", "get_current_time"]
    arguments: dict[str, str]
    reason: str


class PreReviewResult(BaseModel):
    """Output from the pre-fetch reviewer pass."""

    triggered_rules: list[str]
    prefetch: list[PrefetchAction]
    reminders: list[str]


class RequiredAction(BaseModel):
    """A machine-verifiable action that the responder must complete."""

    code: str
    description: str
    tool: Literal[
        "get_current_time",
        "execute_shell",
        "read_file",
        "write_file",
        "edit_file",
        "memory_edit",
        "write_or_edit",
    ]
    target_path: str | None = None
    target_path_glob: str | None = None
    command_must_contain: str | None = None
    index_path: str | None = None


class PostReviewResult(BaseModel):
    """Output from the post-review pass."""

    passed: bool
    violations: list[str]
    required_actions: list[RequiredAction] = []
    retry_instruction: str = ""
    # Backward-compatible fallback for older prompts/models.
    guidance: str | None = None
