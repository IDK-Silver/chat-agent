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


class PostReviewResult(BaseModel):
    """Output from the post-review pass."""

    passed: bool
    violations: list[str]
    guidance: str
