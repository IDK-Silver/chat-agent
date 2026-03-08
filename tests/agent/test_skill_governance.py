from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_responder
from chat_agent.agent.skill_governance import (
    SKILL_PREREQUISITE_TOOL_NAME,
    SkillGovernanceRegistry,
)
from chat_agent.agent.turn_context import TurnContext
from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.core.schema import ToolsConfig
from chat_agent.llm.schema import LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from chat_agent.tools.registry import ToolResult


def _write_discord_skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "kernel" / "builtin-skills" / "discord-messaging"
    skill_dir.mkdir(parents=True)
    (skill_dir / "guide.md").write_text("discord guide body", encoding="utf-8")
    (skill_dir / "meta.yaml").write_text(
        "\n".join(
            [
                "id: discord-messaging",
                "guide: guide.md",
                "governs:",
                "  - tool: send_message",
                "    when:",
                "      channel: discord",
                "    enforcement: require_context",
            ]
        ),
        encoding="utf-8",
    )
    return skill_dir / "guide.md"


def _console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _base_messages(conversation: Conversation, builder: ContextBuilder) -> list[Message]:
    return builder.build(conversation)


def _tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="send_message",
            description="send",
            parameters={
                "channel": ToolParameter(type="string", description="channel"),
                "body": ToolParameter(type="string", description="body"),
            },
            required=["channel", "body"],
        ),
        ToolDefinition(
            name="read_file",
            description="read",
            parameters={"path": ToolParameter(type="string", description="path")},
            required=["path"],
        ),
    ]


class _Client:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    def chat_with_tools(self, messages, tools, temperature=None):
        del tools, temperature
        self.calls.append(list(messages))
        if not self._responses:
            raise RuntimeError("no response queued")
        return self._responses.pop(0)


class _Registry:
    def __init__(self, results: dict[str, str]):
        self._results = dict(results)
        self.executed: list[str] = []

    def has_tool(self, name):
        return name in self._results

    def execute(self, tool_call):
        self.executed.append(tool_call.name)
        content = self._results[tool_call.name]
        is_error = isinstance(content, str) and content.startswith("Error")
        return ToolResult(content, is_error=is_error)


def test_skill_registry_matches_conditional_send_message(tmp_path: Path):
    guide_path = _write_discord_skill(tmp_path)
    registry = SkillGovernanceRegistry.load(tmp_path)

    requirements = registry.requirements_for_tool_call(
        ToolCall(
            id="t1",
            name="send_message",
            arguments={"channel": "discord", "body": "hi"},
        )
    )
    assert [item.skill_id for item in requirements] == ["discord-messaging"]
    assert requirements[0].guide_rel_path == "kernel/builtin-skills/discord-messaging/guide.md"

    assert registry.requirements_for_tool_call(
        ToolCall(
            id="t2",
            name="send_message",
            arguments={"channel": "gmail", "body": "hi"},
        )
    ) == []
    assert registry.note_loaded_guide(path=str(guide_path)) == "discord-messaging"


def test_run_responder_injects_required_skill_before_discord_send(tmp_path: Path):
    _write_discord_skill(tmp_path)
    skill_registry = SkillGovernanceRegistry.load(tmp_path)

    conversation = Conversation()
    conversation.add("user", "hello", channel="discord", sender="alice")
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    turn_context = TurnContext()
    turn_context.set_inbound("discord", "alice", {})

    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
        ]
    )
    registry = _Registry({"send_message": "OK: sent to discord"})

    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=turn_context,
    )

    assert response.finish_reason == "terminal_tool_short_circuit"
    assert registry.executed == ["send_message"]
    assert len(client.calls) == 2
    assert any(
        msg.role == "tool"
        and msg.name == SKILL_PREREQUISITE_TOOL_NAME
        and "discord guide body" in str(msg.content)
        for msg in client.calls[1]
    )


def test_read_file_of_skill_guide_marks_turn_as_loaded(tmp_path: Path):
    guide_path = _write_discord_skill(tmp_path)
    skill_registry = SkillGovernanceRegistry.load(tmp_path)

    conversation = Conversation()
    conversation.add("user", "hello", channel="discord", sender="alice")
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    turn_context = TurnContext()
    turn_context.set_inbound("discord", "alice", {})

    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="read_file",
                        arguments={"path": str(guide_path)},
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
        ]
    )
    registry = _Registry(
        {
            "read_file": '<file path="kernel/builtin-skills/discord-messaging/guide.md">discord guide body</file>',
            "send_message": "OK: sent to discord",
        }
    )

    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=turn_context,
    )

    assert response.finish_reason == "terminal_tool_short_circuit"
    assert registry.executed == ["read_file", "send_message"]
    assert len(client.calls) == 2
    assert all(
        not any(msg.name == SKILL_PREREQUISITE_TOOL_NAME for msg in call if msg.role == "tool")
        for call in client.calls
    )


def test_second_turn_reuses_existing_injected_skill_guide(tmp_path: Path):
    _write_discord_skill(tmp_path)
    skill_registry = SkillGovernanceRegistry.load(tmp_path)

    conversation = Conversation()
    conversation.add("user", "hello", channel="discord", sender="alice")
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    registry = _Registry({"send_message": "OK: sent to discord"})

    first_turn_context = TurnContext()
    first_turn_context.set_inbound("discord", "alice", {})
    first_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
        ]
    )

    first_response = _run_responder(
        client=first_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=first_turn_context,
    )

    assert first_response.finish_reason == "terminal_tool_short_circuit"
    assert len(first_client.calls) == 2
    initial_injected_count = sum(
        1
        for entry in conversation.get_messages()
        if entry.role == "tool" and entry.name == SKILL_PREREQUISITE_TOOL_NAME
    )

    conversation.add("user", "again", channel="discord", sender="alice")
    second_turn_context = TurnContext()
    second_turn_context.set_inbound("discord", "alice", {})
    second_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t3",
                        name="send_message",
                        arguments={"channel": "discord", "body": "second"},
                    )
                ],
            ),
        ]
    )

    second_response = _run_responder(
        client=second_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=second_turn_context,
    )

    assert second_response.finish_reason == "terminal_tool_short_circuit"
    assert len(second_client.calls) == 1
    assert sum(
        1
        for entry in conversation.get_messages()
        if entry.role == "tool" and entry.name == SKILL_PREREQUISITE_TOOL_NAME
    ) == initial_injected_count


def test_second_turn_reuses_prior_read_file_guide_from_conversation(tmp_path: Path):
    guide_path = _write_discord_skill(tmp_path)
    skill_registry = SkillGovernanceRegistry.load(tmp_path)

    conversation = Conversation()
    conversation.add("user", "hello", channel="discord", sender="alice")
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    registry = _Registry(
        {
            "read_file": '<file path="kernel/builtin-skills/discord-messaging/guide.md">discord guide body</file>',
            "send_message": "OK: sent to discord",
        }
    )

    first_turn_context = TurnContext()
    first_turn_context.set_inbound("discord", "alice", {})
    first_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="read_file",
                        arguments={"path": str(guide_path)},
                    )
                ],
            ),
            LLMResponse(content=None, tool_calls=[]),
        ]
    )

    first_response = _run_responder(
        client=first_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=first_turn_context,
    )

    assert first_response.tool_calls == []

    conversation.add("user", "send now", channel="discord", sender="alice")
    second_turn_context = TurnContext()
    second_turn_context.set_inbound("discord", "alice", {})
    second_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
        ]
    )

    second_response = _run_responder(
        client=second_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=second_turn_context,
    )

    assert second_response.finish_reason == "terminal_tool_short_circuit"
    assert len(second_client.calls) == 1
    assert not any(
        msg.role == "tool" and msg.name == SKILL_PREREQUISITE_TOOL_NAME
        for msg in second_client.calls[0]
    )


def test_compaction_drops_guide_and_next_turn_reinjects(tmp_path: Path):
    _write_discord_skill(tmp_path)
    skill_registry = SkillGovernanceRegistry.load(tmp_path)

    conversation = Conversation()
    conversation.add("user", "hello", channel="discord", sender="alice")
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    registry = _Registry({"send_message": "OK: sent to discord"})

    first_turn_context = TurnContext()
    first_turn_context.set_inbound("discord", "alice", {})
    first_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="send_message",
                        arguments={"channel": "discord", "body": "hi"},
                    )
                ],
            ),
        ]
    )

    first_response = _run_responder(
        client=first_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=first_turn_context,
    )

    assert first_response.finish_reason == "terminal_tool_short_circuit"
    assert len(first_client.calls) == 2
    assert any(
        entry.role == "tool" and entry.name == SKILL_PREREQUISITE_TOOL_NAME
        for entry in conversation.get_messages()
    )

    conversation.add("user", "placeholder", channel="discord", sender="alice")
    removed = conversation.compact(1)
    assert removed > 0
    assert not any(
        entry.role == "tool" and entry.name == SKILL_PREREQUISITE_TOOL_NAME
        for entry in conversation.get_messages()
    )

    conversation.add("user", "send again", channel="discord", sender="alice")
    second_turn_context = TurnContext()
    second_turn_context.set_inbound("discord", "alice", {})
    second_client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t3",
                        name="send_message",
                        arguments={"channel": "discord", "body": "again"},
                    )
                ],
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t4",
                        name="send_message",
                        arguments={"channel": "discord", "body": "again"},
                    )
                ],
            ),
        ]
    )

    second_response = _run_responder(
        client=second_client,  # type: ignore[arg-type]
        messages=_base_messages(conversation, builder),
        tools=_tool_definitions(),
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
        skill_registry=skill_registry,
        turn_context=second_turn_context,
    )

    assert second_response.finish_reason == "terminal_tool_short_circuit"
    assert len(second_client.calls) == 2
