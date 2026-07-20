"""Deploy brain prompt rewrite emphasizing think-then-act with send_message."""

import shutil
from pathlib import Path

from .base import Migration


class M0104BrainPromptThinkThenAct(Migration):
    """Refresh brain system prompt with concise think-then-act wording."""

    version = "0.57.9"
    summary = (
        "更新 Brain 系統提示：改為「充分思考後再行動」，"
        "工具規則改成先判斷再呼叫，且明確要求只有 send_message 才算對外傳達"
    )

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
