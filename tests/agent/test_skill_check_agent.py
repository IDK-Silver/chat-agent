from chat_agent.agent.skill_check import SkillCheckAgent
from chat_agent.agent.skill_governance import SkillCatalogEntry


class _Client:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def chat(self, messages, response_schema=None, temperature=None):
        del response_schema, temperature
        self.calls.append(messages)
        return self.response


def test_skill_check_agent_picks_exact_skill_names():
    client = _Client("- skill-creator\n- agent-browser")
    agent = SkillCheckAgent(client, "sys")

    result = agent.pick_skill_names(
        latest_user_input="Fix SKILL.md format in personal-skills.",
        skills=[
            SkillCatalogEntry(name="skill-creator", description="Skill maintenance."),
            SkillCatalogEntry(name="agent-browser", description="Browser automation."),
        ],
        max_skills=1,
    )

    assert result == ["skill-creator"]


def test_skill_check_agent_returns_empty_for_none():
    client = _Client("NONE")
    agent = SkillCheckAgent(client, "sys")

    result = agent.pick_skill_names(
        latest_user_input="Say hello.",
        skills=[SkillCatalogEntry(name="skill-creator", description="Skill maintenance.")],
        max_skills=1,
    )

    assert result == []


def test_skill_check_agent_falls_back_to_embedded_skill_name():
    client = _Client("Use skill-creator because this request is about editing SKILL.md.")
    agent = SkillCheckAgent(client, "sys")

    result = agent.pick_skill_names(
        latest_user_input="Fix SKILL.md format in personal-skills.",
        skills=[
            SkillCatalogEntry(name="skill-creator", description="Skill maintenance."),
            SkillCatalogEntry(name="skill-installer", description="Skill installation."),
        ],
        max_skills=1,
    )

    assert result == ["skill-creator"]


def test_skill_check_agent_treats_none_with_explanation_as_empty():
    client = _Client("NONE - no skill is clearly needed here.")
    agent = SkillCheckAgent(client, "sys")

    result = agent.pick_skill_names(
        latest_user_input="Say hello.",
        skills=[SkillCatalogEntry(name="skill-creator", description="Skill maintenance.")],
        max_skills=1,
    )

    assert result == []
