"""Shared SKILL.md metadata parsing."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
import yaml


SKILL_ENTRY_FILE = "SKILL.md"
SKILL_METADATA_FILE = "meta.yaml"  # fallback only when SKILL.md absent

_FRONTMATTER_RE = re.compile(
    r"\A(?:\ufeff)?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)


def parse_skill_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a SKILL.md file."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        parsed = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SkillMetadata(BaseModel):
    """Machine-readable metadata from a skill's SKILL.md frontmatter."""

    name: str = Field(max_length=64)
    description: str = Field(default="", max_length=1024)
