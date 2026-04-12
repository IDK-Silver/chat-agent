"""Deploy brain prompt guidance for Notes markdown templates."""

from pathlib import Path
import shutil

from .base import Migration


class M0150NotesTemplateMarkdown(Migration):
    """Refresh prompt text for notes_tool template_markdown support."""

    version = "0.74.8"
    summary = "notes_tool 新增 template_markdown / variables / images，支援可控版型與圖片順序"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
