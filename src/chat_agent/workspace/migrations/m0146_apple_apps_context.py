"""Deploy brain prompt updates for Calendar/Reminders context integration."""

import json
import shutil
from pathlib import Path

from .base import Migration


class M0146AppleAppsContext(Migration):
    """Copy updated brain prompt and seed apple apps context state file."""

    version = "0.74.4"
    summary = (
        "Brain prompt 新增 Calendar/Reminders 摘要 note 與 source metadata 指引；"
        "workspace state 新增 apple_apps_context.json。"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        state_dir = kernel_dir.parent / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "apple_apps_context.json"
        if state_path.exists():
            return
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"last_refresh_at": None}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(state_path)
