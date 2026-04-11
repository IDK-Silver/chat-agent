"""Remove Calendar/Reminders auto-sync guidance from the live kernel."""

from pathlib import Path
import shutil

from .base import Migration


class M0148RemoveAppleAppsAutoSync(Migration):
    """Deploy updated prompt text and clean stale auto-sync state."""

    version = "0.74.6"
    summary = "移除 Calendar/Reminders 自動同步到 agent_note 的 runtime 行為與 prompt 說明"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        state_path = kernel_dir.parent / "state" / "apple_apps_context.json"
        state_path.unlink(missing_ok=True)
