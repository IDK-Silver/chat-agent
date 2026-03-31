"""Helpers for filesystem-backed skill indexes."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .metadata import SKILL_ENTRY_FILE, SkillMetadata, parse_skill_frontmatter


BUILTIN_SKILLS_DIR = "kernel/builtin-skills"
PERSONAL_SKILLS_DIR = "personal-skills"
PERSONAL_SKILLS_INDEX_REL_PATH = f"{PERSONAL_SKILLS_DIR}/index.md"


def rebuild_personal_skills_index(agent_os_dir: Path) -> Path:
    """Rebuild personal-skills/index.md from SKILL.md frontmatter."""
    root = agent_os_dir / PERSONAL_SKILLS_DIR
    root.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / SKILL_ENTRY_FILE
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            metadata = SkillMetadata.model_validate(parse_skill_frontmatter(text))
        except (OSError, ValidationError):
            continue
        entries.append(
            (
                metadata.name,
                f"./{child.name}/{SKILL_ENTRY_FILE}",
                metadata.description.strip(),
            )
        )

    lines = [
        "# 個人技能索引",
        "",
        "用戶建立或 agent 自己整理的本地技能。",
        "",
        "## 技能",
        "",
    ]
    for name, rel_path, description in entries:
        suffix = f" — {description}" if description else ""
        lines.append(f"- [{name}]({rel_path}){suffix}")
    if not entries:
        lines.append("- （目前沒有個人技能）")
    lines.append("")

    index_path = root / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path
