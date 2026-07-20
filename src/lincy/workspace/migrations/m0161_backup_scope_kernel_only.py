"""Upgrade backup scope narrowed to upgrade-managed dirs: notice only."""

from pathlib import Path

from .base import Migration


class M0161BackupScopeKernelOnly(Migration):
    """Announce the narrowed pre-migration backup scope (code-only)."""

    version = "0.74.19"
    summary = (
        "kernel 升級前的備份改為只備 kernel/、memory/、personal-skills/，"
        "不再複製 state/（含 discord 媒體）與 cache/。先前全量備份在 "
        "iCloud workspace 上會因 dataless 媒體檔觸發隨選下載而卡死升級，"
        "且體積無上限。backups/ 內既有的舊全量備份可手動清理釋放空間"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pass
