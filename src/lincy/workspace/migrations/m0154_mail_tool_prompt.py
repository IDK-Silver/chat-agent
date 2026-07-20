"""Refresh brain prompt with macOS Mail tool guidance."""

from pathlib import Path
import shutil

from .base import Migration


class M0154MailToolPrompt(Migration):
    """Deploy brain prompt guidance for mail_tool scope, search, and trash rules."""

    version = "0.74.12"
    summary = "Brain prompt: 新增 mail_tool 使用規則，限制查詢範圍並保守刪信"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
