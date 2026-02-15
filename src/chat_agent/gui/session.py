"""GUI session persistence: step-by-step recording for resume and reporting."""

import os
from datetime import datetime, timezone as tz
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel


class GUIStepRecord(BaseModel):
    """One tool invocation within a GUI session."""

    tool: str
    args: dict[str, Any]
    result: str


class GUISessionData(BaseModel):
    """Persistent state for a single GUI task session."""

    session_id: str
    intent: str
    status: Literal["active", "completed", "failed"] = "active"
    summary: str = ""
    report: str = ""
    steps: list[GUIStepRecord] = []
    steps_used: int = 0
    created_at: datetime
    updated_at: datetime


def _generate_gui_session_id() -> str:
    """Generate a time-sortable session ID: YYYYMMDD_HHMMSS_<6-hex>."""
    now = datetime.now(tz.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    suffix = os.urandom(3).hex()
    return f"{timestamp}_{suffix}"


class GUISessionStore:
    """Manages GUI session files under session/gui/."""

    def __init__(self, gui_sessions_dir: Path) -> None:
        self._dir = gui_sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(self, intent: str) -> GUISessionData:
        """Create a new GUI session and persist it."""
        now = datetime.now(tz.utc)
        data = GUISessionData(
            session_id=_generate_gui_session_id(),
            intent=intent,
            created_at=now,
            updated_at=now,
        )
        self._save(data)
        return data

    def load(self, session_id: str) -> GUISessionData:
        """Load an existing GUI session by ID."""
        path = self._path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"GUI session not found: {session_id}")
        return GUISessionData.model_validate_json(path.read_text(encoding="utf-8"))

    def append_step(self, session_id: str, step: GUIStepRecord) -> None:
        """Append a step record and persist immediately."""
        data = self.load(session_id)
        data.steps.append(step)
        data.steps_used = len(data.steps)
        data.updated_at = datetime.now(tz.utc)
        self._save(data)

    def finalize(
        self,
        session_id: str,
        *,
        success: bool,
        summary: str,
        report: str = "",
    ) -> None:
        """Mark a session as completed or failed."""
        data = self.load(session_id)
        data.status = "completed" if success else "failed"
        data.summary = summary
        data.report = report
        data.updated_at = datetime.now(tz.utc)
        self._save(data)

    def format_steps_as_context(self, data: GUISessionData) -> str:
        """Format completed steps for injection into a resume prompt."""
        if not data.steps:
            return ""
        lines = [f"Previous progress on this task ({len(data.steps)} steps completed):"]
        for i, step in enumerate(data.steps, 1):
            args_str = ", ".join(f'{k}="{v}"' for k, v in step.args.items())
            lines.append(f"{i}. [{step.tool}] {args_str} -> {step.result}")
        return "\n".join(lines)

    def _path_for(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _save(self, data: GUISessionData) -> None:
        path = self._path_for(data.session_id)
        path.write_text(
            data.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
