"""Deploy Apple Notes markdown/search guidance and cache directories."""

from pathlib import Path
import shutil

from .base import Migration


class M0149AppleNotesCache(Migration):
    """Refresh prompt text and ensure Apple Notes / Vision cache directories exist."""

    version = "0.74.7"
    summary = "notes_tool 改為 Markdown 內容與摘要搜尋，並新增 apple_notes / vision cache"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        cache_dir = kernel_dir.parent / "cache"
        (cache_dir / "apple_notes").mkdir(parents=True, exist_ok=True)
        (cache_dir / "vision").mkdir(parents=True, exist_ok=True)
