"""Refresh brain prompt guidance for Notes title/body separation rules."""

from pathlib import Path
import shutil

from .base import Migration


class M0152NotesTitleBodyRules(Migration):
    """Explain how notes_tool title and template_markdown should be combined."""

    version = "0.74.10"
    summary = "notes_tool 明確區分 title 與正文，避免正文重複主標題"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
