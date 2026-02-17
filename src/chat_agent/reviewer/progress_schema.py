"""Pydantic models for progress-reviewer input/output."""

from pydantic import BaseModel


class ProgressReviewResult(BaseModel):
    """Advisory output from the progress-review pass for visible text chunks."""

    passed: bool
    violations: list[str]
    block_instruction: str = ""
