"""Runtime skill prerequisite governance for tool execution."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal
import uuid

from pydantic import BaseModel, Field, ValidationError
import yaml

from ..llm.schema import Message, ToolCall, make_tool_result_message

if TYPE_CHECKING:
    from ..context.conversation import Conversation

logger = logging.getLogger(__name__)

SKILL_PREREQUISITE_TOOL_NAME = "_load_skill_prerequisite"
_SKILL_METADATA_FILE = "meta.yaml"
_BUILTIN_SKILLS_DIR = "kernel/builtin-skills"
_PERSONAL_SKILLS_DIR = "memory/agent/skills"

_ScalarValue = str | int | float | bool


class GovernedToolRule(BaseModel):
    """One tool-governance rule declared by a skill."""

    tool: str
    when: dict[str, _ScalarValue] = Field(default_factory=dict)
    enforcement: Literal["advisory", "require_context"] = "require_context"


class SkillMetadata(BaseModel):
    """Machine-readable metadata beside a skill guide."""

    id: str
    guide: str
    governs: list[GovernedToolRule] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillRequirement:
    """Resolved prerequisite for a governed tool call."""

    skill_id: str
    guide_path: Path
    guide_rel_path: str


@dataclass(frozen=True)
class InjectedSkillGuide:
    """Synthetic assistant/tool pair for one injected skill guide."""

    call: ToolCall
    content: str
    skill_id: str


@dataclass(frozen=True)
class _RegisteredSkill:
    """Runtime registration for one skill package."""

    metadata: SkillMetadata
    guide_path: Path
    guide_rel_path: str


class SkillGovernanceRegistry:
    """Registry of skills that govern tool usage."""

    def __init__(self, *, agent_os_dir: Path, skills: dict[str, _RegisteredSkill]):
        self._agent_os_dir = agent_os_dir
        self._skills = skills
        self._guide_index = {
            skill.guide_path.resolve(): skill.metadata.id
            for skill in skills.values()
        }

    @classmethod
    def load(cls, agent_os_dir: Path) -> "SkillGovernanceRegistry":
        """Load skill metadata from builtin and personal skill roots."""
        skills: dict[str, _RegisteredSkill] = {}
        for rel_root in (_BUILTIN_SKILLS_DIR, _PERSONAL_SKILLS_DIR):
            root = agent_os_dir / rel_root
            if not root.exists():
                continue
            for meta_path in sorted(root.rglob(_SKILL_METADATA_FILE)):
                registered = _load_skill_metadata(agent_os_dir, meta_path)
                if registered is None:
                    continue
                existing = skills.get(registered.metadata.id)
                if existing is not None:
                    logger.warning(
                        "Duplicate skill id '%s'; overriding %s with %s",
                        registered.metadata.id,
                        existing.guide_rel_path,
                        registered.guide_rel_path,
                    )
                skills[registered.metadata.id] = registered
        return cls(agent_os_dir=agent_os_dir, skills=skills)

    def find_missing_requirements(
        self,
        tool_calls: list[ToolCall],
        *,
        loaded_skill_ids: set[str],
    ) -> list[SkillRequirement]:
        """Return unique missing prerequisites for the given tool batch."""
        ordered: list[SkillRequirement] = []
        seen: set[str] = set()
        for tool_call in tool_calls:
            for requirement in self.requirements_for_tool_call(tool_call):
                if requirement.skill_id in loaded_skill_ids or requirement.skill_id in seen:
                    continue
                seen.add(requirement.skill_id)
                ordered.append(requirement)
        return ordered

    def requirements_for_tool_call(self, tool_call: ToolCall) -> list[SkillRequirement]:
        """Return all enforced prerequisites for one tool call."""
        matches: list[SkillRequirement] = []
        for skill in self._skills.values():
            for rule in skill.metadata.governs:
                if rule.tool != tool_call.name:
                    continue
                if rule.enforcement != "require_context":
                    continue
                if not _rule_matches_arguments(rule, tool_call.arguments):
                    continue
                matches.append(
                    SkillRequirement(
                        skill_id=skill.metadata.id,
                        guide_path=skill.guide_path,
                        guide_rel_path=skill.guide_rel_path,
                    )
                )
        return matches

    def note_loaded_guide(self, *, path: str) -> str | None:
        """Return skill_id when a read_file path matches a governed guide."""
        target = Path(path)
        if not target.is_absolute():
            target = self._agent_os_dir / target
        try:
            resolved = target.resolve()
        except Exception:
            resolved = target.resolve(strict=False)
        return self._guide_index.get(resolved)

    def loaded_skill_ids_from_conversation(
        self,
        conversation: "Conversation",
    ) -> set[str]:
        """Return skill ids whose guides are still present in conversation."""
        loaded: set[str] = set()
        pending_injected: dict[str, str] = {}
        pending_reads: dict[str, str] = {}

        for entry in conversation.get_messages():
            if entry.role == "assistant" and entry.tool_calls:
                for tool_call in entry.tool_calls:
                    if tool_call.name == SKILL_PREREQUISITE_TOOL_NAME:
                        skill_id = tool_call.arguments.get("skill_id")
                        if isinstance(skill_id, str):
                            pending_injected[tool_call.id] = skill_id
                        continue
                    if tool_call.name != "read_file":
                        continue
                    path = tool_call.arguments.get("path")
                    if not isinstance(path, str):
                        continue
                    skill_id = self.note_loaded_guide(path=path)
                    if skill_id is not None:
                        pending_reads[tool_call.id] = skill_id
                continue

            if entry.role != "tool" or not isinstance(entry.tool_call_id, str):
                continue

            injected_skill_id = pending_injected.get(entry.tool_call_id)
            if injected_skill_id is not None and entry.name == SKILL_PREREQUISITE_TOOL_NAME:
                loaded.add(injected_skill_id)

            read_skill_id = pending_reads.get(entry.tool_call_id)
            if read_skill_id is not None and entry.name == "read_file":
                loaded.add(read_skill_id)
        return loaded

    def build_injected_guides(
        self,
        requirements: list[SkillRequirement],
    ) -> list[InjectedSkillGuide]:
        """Build synthetic assistant/tool pairs for required guides."""
        injected: list[InjectedSkillGuide] = []
        for requirement in requirements:
            content = _load_guide_content(requirement)
            if content is None:
                continue
            call = ToolCall(
                id=f"skill_{uuid.uuid4().hex[:8]}",
                name=SKILL_PREREQUISITE_TOOL_NAME,
                arguments={
                    "skill_id": requirement.skill_id,
                    "path": requirement.guide_rel_path,
                },
            )
            injected.append(
                InjectedSkillGuide(
                    call=call,
                    content=content,
                    skill_id=requirement.skill_id,
                )
            )
        return injected


def build_skill_prerequisite_messages(
    injected: InjectedSkillGuide,
) -> tuple[Message, Message]:
    """Build a synthetic assistant/tool pair for one loaded skill guide."""
    call_msg = Message(
        role="assistant",
        content=None,
        tool_calls=[injected.call],
    )
    result_msg = make_tool_result_message(
        tool_call_id=injected.call.id,
        name=injected.call.name,
        content=injected.content,
    )
    return call_msg, result_msg


def build_skill_deferral_text(
    *,
    missing_skill_ids: list[str],
) -> str:
    """Build tool deferral text when prerequisites were injected first."""
    joined = ", ".join(missing_skill_ids)
    return (
        "Error: Deferred this tool round until required skill guide(s) "
        f"were loaded into context: {joined}. Review the loaded guide and "
        "retry any tool calls that are still appropriate."
    )


def _load_skill_metadata(
    agent_os_dir: Path,
    meta_path: Path,
) -> _RegisteredSkill | None:
    try:
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        metadata = SkillMetadata.model_validate(raw)
    except (OSError, ValidationError, yaml.YAMLError) as error:
        logger.warning("Skipping invalid skill metadata %s: %s", meta_path, error)
        return None

    guide_path = (meta_path.parent / metadata.guide).resolve(strict=False)
    if not guide_path.exists():
        logger.warning(
            "Skipping skill '%s': guide file missing at %s",
            metadata.id,
            guide_path,
        )
        return None
    try:
        guide_rel_path = str(guide_path.relative_to(agent_os_dir))
    except ValueError:
        logger.warning(
            "Skipping skill '%s': guide path %s is outside agent_os_dir %s",
            metadata.id,
            guide_path,
            agent_os_dir,
        )
        return None
    return _RegisteredSkill(
        metadata=metadata,
        guide_path=guide_path,
        guide_rel_path=guide_rel_path,
    )


def _rule_matches_arguments(
    rule: GovernedToolRule,
    arguments: dict[str, object],
) -> bool:
    for key, expected in rule.when.items():
        if arguments.get(key) != expected:
            return False
    return True


def _load_guide_content(requirement: SkillRequirement) -> str | None:
    try:
        content = requirement.guide_path.read_text(encoding="utf-8").rstrip()
    except OSError as error:
        logger.warning(
            "Failed to load required skill '%s' from %s: %s",
            requirement.skill_id,
            requirement.guide_path,
            error,
        )
        return None

    return (
        "[Required Skill Guide Loaded]\n"
        f"skill_id: {requirement.skill_id}\n"
        f"path: {requirement.guide_rel_path}\n\n"
        f'<file path="{requirement.guide_rel_path}">\n'
        f"{content}\n"
        "</file>"
    )
