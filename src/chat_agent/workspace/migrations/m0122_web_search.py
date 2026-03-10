"""Deploy web_search guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0122WebSearch(Migration):
    """Copy updated brain system.md with web_search guidance."""

    version = "0.65.0"
    summary = "更新 brain prompt：新增 web_search 外部查證工具與使用指引"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
