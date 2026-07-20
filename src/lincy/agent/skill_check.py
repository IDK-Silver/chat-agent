"""Prompt-only sub-agent for proactive skill selection."""

from __future__ import annotations

import re

from .skill_governance import SkillCatalogEntry
from ..llm.base import LLMClient
from ..llm.schema import Message

_NONE_RESPONSE = "NONE"
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)+")


class SkillCheckAgent:
    """Sub-agent that picks relevant skills from metadata only."""

    def __init__(self, client: LLMClient, system_prompt: str):
        self.client = client
        self.system_prompt = system_prompt

    def pick_skill_names(
        self,
        *,
        latest_user_input: str,
        skills: list[SkillCatalogEntry],
        loaded_skill_names: set[str] | None = None,
        max_skills: int = 1,
    ) -> list[str]:
        """Return exact skill names to inject for the current user turn."""
        if max_skills <= 0 or not latest_user_input.strip() or not skills:
            return []

        loaded = sorted(name for name in (loaded_skill_names or set()) if name)
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=_build_selection_prompt(
                    latest_user_input=latest_user_input,
                    skills=skills,
                    loaded_skill_names=loaded,
                    max_skills=max_skills,
                ),
            ),
        ]
        response = self.client.chat(messages)
        return _parse_skill_names(
            response,
            allowed_names={item.name for item in skills},
            max_skills=max_skills,
        )


def _build_selection_prompt(
    *,
    latest_user_input: str,
    skills: list[SkillCatalogEntry],
    loaded_skill_names: list[str],
    max_skills: int,
) -> str:
    """Build the user message sent to the skill-check sub-agent."""
    lines = [
        "Latest user request:",
        latest_user_input.strip(),
        "",
        "Already loaded skills (do not repeat):",
    ]
    if loaded_skill_names:
        lines.extend(f"- {name}" for name in loaded_skill_names)
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "Available skills:",
        ]
    )
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")

    lines.extend(
        [
            "",
            f"Select up to {max_skills} skill names that should be loaded before the main brain responds.",
            "If no skill is clearly needed, reply exactly:",
            _NONE_RESPONSE,
            "",
            "Otherwise reply with exact skill names only, one per line, most relevant first.",
        ]
    )
    return "\n".join(lines)


def _parse_skill_names(
    raw: str,
    *,
    allowed_names: set[str],
    max_skills: int,
) -> list[str]:
    """Parse an exact-name skill list from the sub-agent response.

    Strategy:
    1. Exact line / prefix matches first
    2. If nothing matched, scan the full response for exact skill-name mentions
    3. Any explicit NONE-style response wins and returns []
    """
    picked: list[str] = []
    if not raw.strip():
        return picked

    ordered_names = sorted(allowed_names, key=len, reverse=True)
    for line in raw.splitlines():
        cleaned = _normalize_line(line)
        if not cleaned:
            continue
        if _is_none_response(cleaned):
            return []
        name = _extract_exact_name(cleaned, ordered_names)
        if name is None or name in picked:
            continue
        picked.append(name)
        if len(picked) >= max_skills:
            break
    if picked:
        return picked
    return _scan_embedded_skill_names(raw, allowed_names=ordered_names, max_skills=max_skills)


def _normalize_line(line: str) -> str:
    """Strip common list markers while preserving exact skill names."""
    cleaned = _BULLET_PREFIX_RE.sub("", line).strip()
    return cleaned.strip("`")


def _is_none_response(text: str) -> bool:
    """Return True when the model explicitly says no skill is needed."""
    upper = text.upper()
    if upper == _NONE_RESPONSE:
        return True
    if not upper.startswith(f"{_NONE_RESPONSE} "):
        return False
    return text[len(_NONE_RESPONSE)].strip()[:1] in {"", ":", "-", "(", ","}


def _extract_exact_name(text: str, allowed_names: list[str]) -> str | None:
    """Return one allowed skill name when the line starts with an exact name."""
    if text in allowed_names:
        return text

    for name in allowed_names:
        if not text.startswith(name):
            continue
        suffix = text[len(name):].strip()
        if not suffix or suffix[0] in {":", "-", "(", ","}:
            return name
    return None


def _scan_embedded_skill_names(
    raw: str,
    *,
    allowed_names: list[str],
    max_skills: int,
) -> list[str]:
    """Best-effort fallback when the model answered in sentences instead of lines."""
    if any(_is_none_response(_normalize_line(line)) for line in raw.splitlines()):
        return []

    matches: list[tuple[int, str]] = []
    for name in allowed_names:
        match = re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9-])",
            raw,
        )
        if match is None:
            continue
        matches.append((match.start(), name))
    matches.sort()

    picked: list[str] = []
    for _, name in matches:
        if name in picked:
            continue
        picked.append(name)
        if len(picked) >= max_skills:
            break
    return picked
