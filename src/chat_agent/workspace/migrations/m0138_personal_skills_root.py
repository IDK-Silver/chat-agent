"""Move personal skills out of memory/ and into personal-skills/."""

from __future__ import annotations

import shutil
from pathlib import Path

from ...skills import PERSONAL_SKILLS_DIR, rebuild_personal_skills_index
from .base import Migration

_DEPLOY_KERNEL = [
    "agents/brain/prompts/system.md",
    "builtin-skills/skill-creator/SKILL.md",
]
_MANAGED_PERSONAL_SKILLS = {"memory-maintenance"}
_LEGACY_PERSONAL_ROOT = Path("memory/agent/skills")


def _copy_file(src: Path, dst: Path) -> None:
    """Copy one file, creating parent directories as needed."""
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _merge_tree(src: Path, dst: Path, *, overwrite: bool) -> None:
    """Merge src into dst, preserving dst unless overwrite=True."""
    if not src.exists():
        return
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not dst.exists():
            shutil.copy2(src, dst)
        return

    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        _merge_tree(child, dst / child.name, overwrite=overwrite)


def _move_legacy_tree(src: Path, dst: Path) -> None:
    """Move a legacy skill tree into dst, preserving any existing dst files."""
    if not src.exists():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return
    if src.is_file():
        src.unlink(missing_ok=True)
        return
    if not dst.is_dir():
        shutil.rmtree(src, ignore_errors=True)
        return

    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        _move_legacy_tree(child, dst / child.name)
    src.rmdir()


def _remove_legacy_skills_link(agent_index_path: Path) -> None:
    """Drop the old skills/ entry from memory/agent/index.md if present."""
    if not agent_index_path.exists():
        return
    text = agent_index_path.read_text(encoding="utf-8")
    kept_lines = [
        line for line in text.splitlines()
        if "[skills/](skills/)" not in line
    ]
    if kept_lines == text.splitlines():
        return
    suffix = "\n" if text.endswith("\n") else ""
    agent_index_path.write_text("\n".join(kept_lines) + suffix, encoding="utf-8")


class M0138PersonalSkillsRoot(Migration):
    """Move personal skills out of memory/ and rebuild the new root index."""

    version = "0.70.0"
    summary = "個人 skills 移出 memory，改由 personal-skills/ 管理並自動重建索引"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        agent_os_dir = kernel_dir.parent
        templates_root = templates_dir.parent
        personal_root = agent_os_dir / PERSONAL_SKILLS_DIR
        legacy_root = agent_os_dir / _LEGACY_PERSONAL_ROOT

        for rel in _DEPLOY_KERNEL:
            _copy_file(templates_dir / rel, kernel_dir / rel)

        personal_root.mkdir(parents=True, exist_ok=True)
        if legacy_root.exists():
            for child in sorted(legacy_root.iterdir()):
                if child.name == "index.md":
                    continue
                _move_legacy_tree(child, personal_root / child.name)
            shutil.rmtree(legacy_root, ignore_errors=True)

        template_personal_root = templates_root / PERSONAL_SKILLS_DIR
        if template_personal_root.exists():
            for child in sorted(template_personal_root.iterdir()):
                overwrite = child.name in _MANAGED_PERSONAL_SKILLS
                _merge_tree(child, personal_root / child.name, overwrite=overwrite)

        _remove_legacy_skills_link(agent_os_dir / "memory" / "agent" / "index.md")
        rebuild_personal_skills_index(agent_os_dir)
