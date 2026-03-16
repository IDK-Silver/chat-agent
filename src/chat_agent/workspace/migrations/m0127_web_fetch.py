"""Deploy web_fetch guidance in the brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0127WebFetch(Migration):
    """Copy updated brain system.md with web_fetch guidance."""

    version = "0.66.4"
    summary = "更新 brain prompt：新增 web_fetch 單頁抓取工具與使用指引"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
