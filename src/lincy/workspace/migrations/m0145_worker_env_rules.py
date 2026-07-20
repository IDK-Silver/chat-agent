"""Update worker system prompt with environment rules and failure handling."""

import shutil
from pathlib import Path

from .base import Migration


class M0145WorkerEnvRules(Migration):
    """Deploy worker prompt with macOS/uv environment rules and early abort."""

    version = "0.74.3"
    summary = "Worker prompt: macOS 環境規則、uv 替代 python/pip、連續失敗自動停止"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "worker" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "worker" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
