"""Refresh brain prompt guidance for Notes template title handling."""

from pathlib import Path
import shutil

from .base import Migration


class M0151NotesTemplateTitleSemantics(Migration):
    """Explain that notes_tool title controls the actual Notes note name."""

    version = "0.74.9"
    summary = "notes_tool template_markdown 明確區分 title 與模板內容，避免 Notes 誤吃第一行當標題"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
