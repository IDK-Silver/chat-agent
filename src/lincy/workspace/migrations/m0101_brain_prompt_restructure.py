"""Deploy brain prompt structural rewrite for person-first decision making."""

import shutil
from pathlib import Path

from .base import Migration


class M0101BrainPromptRestructure(Migration):
    """Update brain system prompt structure to reinforce person-first cognition."""

    version = "0.57.6"
    summary = "重整 Brain 系統提示結構：先感知/理解對方狀態再決定行動，將規則分層為思考流程、說話方式與硬約束"

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
