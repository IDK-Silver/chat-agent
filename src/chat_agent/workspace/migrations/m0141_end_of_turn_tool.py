"""Add end_of_turn tool and tool-call efficiency guidance to brain prompt."""

import shutil
from pathlib import Path

from .base import Migration

_PROMPT_COPIES = [
    ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
]


class M0141EndOfTurnTool(Migration):
    """Deploy updated brain prompt with end_of_turn tool and efficiency rules."""

    version = "0.73.0"
    summary = (
        "Replace terminal_tool_short_circuit with end_of_turn tool; "
        "add tool-call efficiency and shell efficiency guidance to brain prompt"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for src_rel, dst_rel in _PROMPT_COPIES:
            src = templates_dir / src_rel
            dst = kernel_dir / dst_rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
