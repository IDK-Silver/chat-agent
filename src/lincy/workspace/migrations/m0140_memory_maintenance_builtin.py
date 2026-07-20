"""Move memory-maintenance skill to builtin-skills and update prompts."""

import shutil
from pathlib import Path

from .base import Migration

# Prompt templates to copy into kernel
_PROMPT_COPIES = [
    ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
    ("agents/memory_editor/prompts/system.md", "agents/memory_editor/prompts/system.md"),
]

# Builtin skill to deploy
_BUILTIN_SKILL_DIR = "builtin-skills/memory-maintenance"

# Old personal-skills and memory/agent/skills copies to remove
_OLD_SKILL_DIRS = [
    "personal-skills/memory-maintenance",
    "memory/agent/skills/memory-maintenance",
]


class M0140MemoryMaintenanceBuiltin(Migration):
    """Promote memory-maintenance to builtin skill; remove old copies."""

    version = "0.72.0"
    summary = (
        "memory-maintenance skill 升級為內建技能，"
        "possible_duplicates warning 改用 memory_edit 直接去重，"
        "不再開 subprocess；memory_editor planner 加強 old_block 精確複製提示"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # 1. Copy updated prompts
        for src_rel, dst_rel in _PROMPT_COPIES:
            src = templates_dir / src_rel
            dst = kernel_dir / dst_rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # 2. Deploy builtin skill
        src_dir = templates_dir / _BUILTIN_SKILL_DIR
        dst_dir = kernel_dir / _BUILTIN_SKILL_DIR
        if src_dir.is_dir():
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.copytree(src_dir, dst_dir)

        # 3. Copy updated builtin-skills index
        src_index = templates_dir / "builtin-skills" / "index.md"
        dst_index = kernel_dir / "builtin-skills" / "index.md"
        if src_index.exists():
            shutil.copy2(src_index, dst_index)

        # 4. Remove old personal-skills and memory/agent/skills copies
        workspace_dir = kernel_dir.parent
        for rel_dir in _OLD_SKILL_DIRS:
            old_dir = workspace_dir / rel_dir
            if old_dir.is_dir():
                shutil.rmtree(old_dir)
