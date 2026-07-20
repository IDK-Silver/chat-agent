"""Reminders due date timezone fix: pure code change, notice only."""

from pathlib import Path

from .base import Migration


class M0159RemindersDueTimezoneFix(Migration):
    """Announce the reminders_tool due date timezone fix (code-only)."""

    version = "0.74.17"
    summary = (
        "reminders_tool 修復：due 日期先前會被 osascript 以錯誤時區解釋，"
        "設台灣時間會往後偏 8 小時且 update 改不回來；現在 due 一律以 app 時區"
        "的絕對時間寫入，讀回的 due 也會帶 +08:00 offset。先前建錯時間的提醒"
        "並不會自動修正，可用 update 重設 due 一次修正"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pass
