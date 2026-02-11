"""Pydantic models for reviewer input/output."""

from typing import Literal

from pydantic import BaseModel, Field


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
        "memory_search",
        "write_or_edit",
    ]
    target_path: str | None = None
    target_path_glob: str | None = None
    command_must_contain: str | None = None
    index_path: str | None = None


TargetSignalName = Literal[
    "target_short_term",
    "target_inner_state",
    "target_pending_thoughts",
    "target_user_profile",
    "target_persona",
    "target_config",
    "target_knowledge",
    "target_experiences",
    "target_thoughts",
    "target_skills",
    "target_interests",
]


AnomalySignalName = Literal[
    "anomaly_missing_required_target",
    "anomaly_wrong_target_path",
    "anomaly_out_of_contract_path",
    "anomaly_missing_index_update",
    "anomaly_brain_style_meta_text",
]


class TargetSignal(BaseModel):
    """Target file/folder signal emitted by reviewer model for this turn."""

    signal: TargetSignalName
    requires_persistence: bool = True
    reason: str | None = None


class AnomalySignal(BaseModel):
    """Anomaly signal emitted by reviewer model for this turn."""

    signal: AnomalySignalName
    target_signal: TargetSignalName | None = None
    reason: str | None = None


class PostReviewResult(BaseModel):
    """Output from the post-review pass."""

    passed: bool
    violations: list[str]
    required_actions: list[RequiredAction] = Field(default_factory=list)
    retry_instruction: str = ""
    target_signals: list[TargetSignal] = Field(default_factory=list)
    anomaly_signals: list[AnomalySignal] = Field(default_factory=list)
    # Optional reviewer guidance text.
    guidance: str | None = None
