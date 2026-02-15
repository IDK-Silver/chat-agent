"""Reorganize sessions/ -> session/brain/ and create session/gui/."""

import shutil
from pathlib import Path

from .base import Migration


class M0047SessionReorganize(Migration):
    """Move brain sessions into session/brain/ and create session/gui/."""

    version = "0.21.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        working_dir = kernel_dir.parent
        old_sessions = working_dir / "sessions"
        new_brain = working_dir / "session" / "brain"
        new_gui = working_dir / "session" / "gui"

        if old_sessions.exists() and not new_brain.exists():
            new_brain.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_sessions), str(new_brain))

        new_brain.mkdir(parents=True, exist_ok=True)
        new_gui.mkdir(parents=True, exist_ok=True)
