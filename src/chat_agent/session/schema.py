"""Pydantic models for session persistence."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SessionMetadata(BaseModel):
    """Metadata for a persisted chat session."""

    session_id: str
    user_id: str
    display_name: str
    created_at: datetime
    updated_at: datetime
    status: Literal["active", "completed", "exited"] = "active"
    message_count: int = 0
