from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_brain_responder
from chat_agent.agent.staged_planning import (
    PlanAction,
    Stage1GatheringResult,
    Stage2BrainPlan,
    Stage2PlanningResult,
    run_stage1_information_gathering,
)
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from chat_agent.tools.builtin.schedule_action import SCHEDULE_ACTION_DEFINITION


def _fake_console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _fake_config(*, enabled: bool, provider: str = "copilot"):
    return SimpleNamespace(
        features=SimpleNamespace(copilot_brain_staged_planning=enabled),
        agents={"brain": SimpleNamespace(llm=SimpleNamespace(provider=provider))},
    )


def _dummy_plan():
    return Stage2BrainPlan(
        summary="reply briefly",
        facts=["user is sleepy"],
        intent_assessment="casual check-in",
        planned_actions=[
            PlanAction(
                tool="send_message",
                purpose="reply to user",
                required=True,
                max_calls=1,
                arguments_hint={"channel": "discord"},
            )
        ],
        execution_rules=["keep it short"],
    )


def test_run_brain_responder_feature_disabled_uses_legacy(monkeypatch):
    console = _fake_console()
    legacy_response = LLMResponse(content="ok", tool_calls=[])
    calls: list[dict] = []

    def _legacy(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return legacy_response

    monkeypatch.setattr("chat_agent.agent.core._run_responder", _legacy)
    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: (_ for _ in ()).throw(AssertionError("stage1 should not run")),
    )

    result = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys")],
        tools=[],
        conversation=Conversation(),
        builder=MagicMock(),
        registry=MagicMock(),
        console=console,
        config=_fake_config(enabled=False),
        channel="cli",
        sender=None,
    )

    assert result is legacy_response
    assert len(calls) == 1


def test_run_brain_responder_staged_shows_plan_and_keeps_conversation_clean(monkeypatch):
    console = _fake_console()
    convo = Conversation()
    legacy_response = LLMResponse(content=None, tool_calls=[])
    captured: dict = {}

    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: Stage1GatheringResult(
            transcript="[tool_call] read_file {}",
            findings_text="facts",
            tool_calls=1,
            final_response=LLMResponse(content=None, tool_calls=[]),
        ),
    )
    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage2_brain_planning",
        lambda **_: Stage2PlanningResult(plan=_dummy_plan(), raw_response="{}"),
    )

    def _legacy(*args, **kwargs):
        captured["kwargs"] = kwargs
        return legacy_response

    monkeypatch.setattr("chat_agent.agent.core._run_responder", _legacy)

    result = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        tools=[
            ToolDefinition(
                name="send_message",
                description="send",
                parameters={"body": ToolParameter(type="string", description="body")},
                required=["body"],
            )
        ],
        conversation=convo,
        builder=MagicMock(),
        registry=MagicMock(),
        console=console,
        config=_fake_config(enabled=True),
        channel="discord",
        sender="alice",
    )

    assert result is legacy_response
    assert convo.get_messages() == []
    console.print_inner_thoughts.assert_called()
    _, _, shown_text = console.print_inner_thoughts.call_args.args
    assert shown_text.startswith("[PLAN][Stage2]\n")
    overlay = captured["kwargs"]["message_overlay"]
    overlaid = overlay([Message(role="system", content="sys")])
    assert any(
        m.role == "system"
        and isinstance(m.content, str)
        and "Stage 3/3: Execute according to the plan" in m.content
        for m in overlaid
    )


def test_run_brain_responder_stage2_failure_falls_back(monkeypatch):
    console = _fake_console()
    legacy_response = LLMResponse(content="legacy", tool_calls=[])
    legacy_calls: list[dict] = []

    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: Stage1GatheringResult(
            transcript="x",
            findings_text="x",
            tool_calls=0,
            final_response=LLMResponse(content=None, tool_calls=[]),
        ),
    )
    monkeypatch.setattr("chat_agent.agent.core.run_stage2_brain_planning", lambda **_: None)

    def _legacy(*args, **kwargs):
        legacy_calls.append(kwargs)
        return legacy_response

    monkeypatch.setattr("chat_agent.agent.core._run_responder", _legacy)

    result = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys")],
        tools=[],
        conversation=Conversation(),
        builder=MagicMock(),
        registry=MagicMock(),
        console=console,
        config=_fake_config(enabled=True),
        channel="cli",
        sender=None,
    )

    assert result is legacy_response
    assert len(legacy_calls) == 1
    warning_texts = [str(call.args[0]) for call in console.print_warning.call_args_list]
    assert any("Stage 2 planning failed" in text for text in warning_texts)


def test_stage1_schedule_action_is_list_only():
    class _Client:
        def __init__(self):
            self.calls = 0

        def chat_with_tools(self, messages, tools, temperature=None):
            del messages, tools, temperature
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="s1",
                            name="schedule_action",
                            arguments={"action": "add", "reason": "x", "trigger_spec": "2030-01-01T00:00"},
                        )
                    ],
                )
            return LLMResponse(content="done", tool_calls=[])

    class _Registry:
        def __init__(self):
            self.execute_calls = 0

        def has_tool(self, name):
            return name == "schedule_action"

        def execute(self, tool_call):
            del tool_call
            self.execute_calls += 1
            return "SHOULD_NOT_RUN"

    console = _fake_console()
    client = _Client()
    registry = _Registry()

    result = run_stage1_information_gathering(
        client=client,  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        all_tools=[SCHEDULE_ACTION_DEFINITION],
        registry=registry,  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=2,
    )

    assert registry.execute_calls == 0
    assert "only supports action='list'" in result.transcript


def test_run_brain_responder_warns_on_unplanned_stage3_tool(monkeypatch):
    console = _fake_console()
    legacy_response = LLMResponse(content=None, tool_calls=[])
    plan = Stage2BrainPlan(
        summary="read then decide",
        facts=[],
        intent_assessment="check memory first",
        planned_actions=[
            PlanAction(tool="read_file", purpose="inspect", required=False, max_calls=1)
        ],
        execution_rules=[],
    )

    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: Stage1GatheringResult(
            transcript="x",
            findings_text="x",
            tool_calls=0,
            final_response=LLMResponse(content=None, tool_calls=[]),
        ),
    )
    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage2_brain_planning",
        lambda **_: Stage2PlanningResult(plan=plan, raw_response="{}"),
    )

    def _legacy(*args, **kwargs):
        hook = kwargs["on_before_tool_call"]
        hook(ToolCall(id="t1", name="send_message", arguments={"body": "hi"}))
        return legacy_response

    monkeypatch.setattr("chat_agent.agent.core._run_responder", _legacy)

    _ = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        tools=[],
        conversation=Conversation(),
        builder=MagicMock(),
        registry=MagicMock(),
        console=console,
        config=_fake_config(enabled=True),
        channel="cli",
        sender=None,
    )

    warning_texts = [str(call.args[0]) for call in console.print_warning.call_args_list]
    assert any("unplanned tool call: send_message" in text for text in warning_texts)
