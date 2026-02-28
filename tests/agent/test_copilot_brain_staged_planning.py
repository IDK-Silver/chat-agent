from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_brain_responder
from chat_agent.agent.staged_planning import (
    STAGE1_SYNTHETIC_TOOL_NAME,
    Stage1GatheringResult,
    Stage2PlanningResult,
    run_stage1_information_gathering,
    run_stage2_brain_planning,
)
from chat_agent.context.conversation import Conversation
from chat_agent.core.schema import StagedPlanningConfig
from chat_agent.llm.schema import LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from chat_agent.tools.builtin.schedule_action import SCHEDULE_ACTION_DEFINITION


def _fake_console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _fake_config(*, enabled: bool, plan_context_files: list[str] | None = None):
    return SimpleNamespace(
        agents={
            "brain": SimpleNamespace(
                staged_planning=StagedPlanningConfig(
                    enabled=enabled,
                    plan_context_files=plan_context_files or [],
                ),
            ),
        },
    )


def _dummy_plan_text() -> str:
    return (
        "Decision: reply briefly.\n"
        "Facts: user sounds sleepy.\n"
        "Actions: send_message once.\n"
        "Rules: keep it short."
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


def test_run_brain_responder_staged_persists_findings_and_shows_plan(monkeypatch):
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
        lambda **_: Stage2PlanningResult(
            plan_text=_dummy_plan_text(),
            raw_response=_dummy_plan_text(),
        ),
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

    # Stage 1 findings persisted in conversation
    msgs = convo.get_messages()
    assert len(msgs) == 2
    assert msgs[0].role == "assistant"
    assert msgs[1].role == "tool"
    assert msgs[1].name == STAGE1_SYNTHETIC_TOOL_NAME
    assert "facts" in msgs[1].content

    # Plan shown in TUI
    console.print_inner_thoughts.assert_called()
    _, _, shown_text = console.print_inner_thoughts.call_args.args
    assert shown_text.startswith("[PLAN][Stage2]\n")

    # Stage 3 overlay includes both findings and plan
    overlay = captured["kwargs"]["message_overlay"]
    overlaid = overlay([Message(role="system", content="sys")])
    assert any(
        m.role == "system"
        and isinstance(m.content, str)
        and "Stage 1 findings" in m.content
        for m in overlaid
    )
    assert any(
        m.role == "system"
        and isinstance(m.content, str)
        and "Stage 3/3" in m.content
        for m in overlaid
    )


def test_run_brain_responder_plan_context_files_injected(monkeypatch, tmp_path):
    console = _fake_console()
    legacy_response = LLMResponse(content=None, tool_calls=[])
    captured: dict[str, object] = {}
    stage3_captured: dict[str, object] = {}

    long_term = tmp_path / "memory" / "agent" / "long-term.md"
    long_term.parent.mkdir(parents=True, exist_ok=True)
    long_term.write_text(
        "- keep caring naturally\n- do not assume missing facts\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: Stage1GatheringResult(
            transcript="stage1",
            findings_text="facts",
            tool_calls=1,
            final_response=LLMResponse(content=None, tool_calls=[]),
        ),
    )

    def _stage2(**kwargs):
        captured["messages"] = kwargs["messages"]
        return Stage2PlanningResult(
            plan_text=_dummy_plan_text(),
            raw_response=_dummy_plan_text(),
        )

    monkeypatch.setattr("chat_agent.agent.core.run_stage2_brain_planning", _stage2)

    def _legacy(*args, **kwargs):
        stage3_captured["kwargs"] = kwargs
        return legacy_response

    monkeypatch.setattr("chat_agent.agent.core._run_responder", _legacy)

    builder = SimpleNamespace(agent_os_dir=tmp_path)

    result = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        tools=[],
        conversation=Conversation(),
        builder=builder,
        registry=MagicMock(),
        console=console,
        config=_fake_config(
            enabled=True,
            plan_context_files=["memory/agent/long-term.md"],
        ),
        channel="discord",
        sender="alice",
    )

    assert result is legacy_response

    # plan_context_files injected into Stage 2
    stage2_messages = captured["messages"]
    assert isinstance(stage2_messages, list)
    ctx_msgs = [
        m for m in stage2_messages
        if m.role == "system"
        and isinstance(m.content, str)
        and "Planning context" in m.content
    ]
    assert len(ctx_msgs) == 1
    assert '<file path="memory/agent/long-term.md">' in ctx_msgs[0].content
    assert "keep caring naturally" in ctx_msgs[0].content

    # plan_context_files also injected into Stage 3 overlay
    overlay = stage3_captured["kwargs"]["message_overlay"]
    overlaid = overlay([Message(role="system", content="sys")])
    assert any(
        m.role == "system"
        and isinstance(m.content, str)
        and "Planning context" in m.content
        and "keep caring naturally" in m.content
        for m in overlaid
    )


def test_run_brain_responder_plan_context_file_missing_warns_and_continues(monkeypatch, tmp_path):
    console = _fake_console()
    legacy_response = LLMResponse(content=None, tool_calls=[])
    captured: dict[str, object] = {"stage2_called": False}

    monkeypatch.setattr(
        "chat_agent.agent.core.run_stage1_information_gathering",
        lambda **_: Stage1GatheringResult(
            transcript="stage1",
            findings_text="facts",
            tool_calls=1,
            final_response=LLMResponse(content=None, tool_calls=[]),
        ),
    )

    def _stage2(**kwargs):
        captured["stage2_called"] = True
        captured["messages"] = kwargs["messages"]
        return Stage2PlanningResult(
            plan_text=_dummy_plan_text(),
            raw_response=_dummy_plan_text(),
        )

    monkeypatch.setattr("chat_agent.agent.core.run_stage2_brain_planning", _stage2)
    monkeypatch.setattr(
        "chat_agent.agent.core._run_responder",
        lambda *args, **kwargs: legacy_response,
    )

    builder = SimpleNamespace(agent_os_dir=tmp_path)

    result = _run_brain_responder(
        client=MagicMock(),
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        tools=[],
        conversation=Conversation(),
        builder=builder,
        registry=MagicMock(),
        console=console,
        config=_fake_config(
            enabled=True,
            plan_context_files=["memory/agent/long-term.md"],
        ),
        channel="discord",
        sender="alice",
    )

    assert result is legacy_response
    assert captured["stage2_called"] is True
    stage2_messages = captured["messages"]
    assert isinstance(stage2_messages, list)
    assert not any(
        m.role == "system"
        and isinstance(m.content, str)
        and "Planning context" in m.content
        for m in stage2_messages
    )
    warning_texts = [str(call.args[0]) for call in console.print_warning.call_args_list]
    assert any("plan_context_files: skipping" in text for text in warning_texts)


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


def test_stage1_requires_initial_memory_search_when_available():
    class _Client:
        def __init__(self):
            self.calls = 0

        def chat_with_tools(self, messages, tools, temperature=None):
            del messages, tools, temperature
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content="Context already sufficient; no additional lookup needed.",
                    tool_calls=[],
                )
            if self.calls == 2:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="m1",
                            name="memory_search",
                            arguments={"query": "reminder take out trash"},
                        )
                    ],
                )
            return LLMResponse(content="done", tool_calls=[])

    class _Registry:
        def __init__(self):
            self.execute_calls = 0

        def execute(self, tool_call):
            self.execute_calls += 1
            assert tool_call.name == "memory_search"
            return "## memory/people/yufeng/schedule.md\n\n- [17:00] take out trash"

        def has_tool(self, name):
            return name == "memory_search"

    console = _fake_console()
    result = run_stage1_information_gathering(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        all_tools=[
            ToolDefinition(
                name="memory_search",
                description="search memory",
                parameters={"query": ToolParameter(type="string", description="query")},
                required=["query"],
            )
        ],
        registry=_Registry(),  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=3,
    )

    assert result.tool_calls == 1
    assert "[stage1-gate]" in result.findings_text


def test_stage1_retries_when_initial_memory_search_query_is_empty():
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
                            id="m1",
                            name="memory_search",
                            arguments={"query": "   "},
                        )
                    ],
                )
            if self.calls == 2:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="m2",
                            name="memory_search",
                            arguments={"query": "reminder take out trash"},
                        )
                    ],
                )
            return LLMResponse(content="done", tool_calls=[])

    class _Registry:
        def __init__(self):
            self.execute_calls = 0

        def execute(self, tool_call):
            self.execute_calls += 1
            assert tool_call.name == "memory_search"
            return "ok"

        def has_tool(self, name):
            return name == "memory_search"

    console = _fake_console()
    registry = _Registry()
    result = run_stage1_information_gathering(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        all_tools=[
            ToolDefinition(
                name="memory_search",
                description="search memory",
                parameters={"query": ToolParameter(type="string", description="query")},
                required=["query"],
            )
        ],
        registry=registry,  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=3,
    )

    assert registry.execute_calls == 1
    assert "query must be non-empty" in result.findings_text


def test_stage1_can_skip_tool_calls_when_memory_search_unavailable():
    class _Client:
        def chat_with_tools(self, messages, tools, temperature=None):
            del messages, tools, temperature
            return LLMResponse(
                content="Context already sufficient; no additional lookup needed.",
                tool_calls=[],
            )

    class _Registry:
        def execute(self, tool_call):
            raise AssertionError(f"should not execute tool: {tool_call.name}")

        def has_tool(self, name):
            return name == "read_file"

    console = _fake_console()
    result = run_stage1_information_gathering(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        all_tools=[
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={"path": ToolParameter(type="string", description="path")},
                required=["path"],
            )
        ],
        registry=_Registry(),  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=2,
    )

    assert result.tool_calls == 0
    assert "no additional lookup needed" in result.findings_text


def test_stage1_skips_memory_search_gate_when_prior_findings_exist():
    """When skip_memory_search_gate=True, Stage 1 can return without calling memory_search."""

    class _Client:
        def chat_with_tools(self, messages, tools, temperature=None):
            del messages, tools, temperature
            return LLMResponse(
                content="Prior findings are still relevant; no new search needed.",
                tool_calls=[],
            )

    class _Registry:
        def execute(self, tool_call):
            raise AssertionError(f"should not execute tool: {tool_call.name}")

        def has_tool(self, name):
            return name == "memory_search"

    console = _fake_console()
    result = run_stage1_information_gathering(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        all_tools=[
            ToolDefinition(
                name="memory_search",
                description="search memory",
                parameters={"query": ToolParameter(type="string", description="query")},
                required=["query"],
            )
        ],
        registry=_Registry(),  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=2,
        skip_memory_search_gate=True,
    )

    assert result.tool_calls == 0
    assert "[stage1-gate]" not in result.findings_text
    assert "no new search needed" in result.findings_text


def test_stage2_planning_accepts_plain_text():
    class _Client:
        def chat(self, messages):
            del messages
            return "Decision: keep silent now.\nAction: do not send_message."

    console = _fake_console()
    stage1 = Stage1GatheringResult(
        transcript="x",
        findings_text="x",
        tool_calls=0,
        final_response=LLMResponse(content=None, tool_calls=[]),
    )

    result = run_stage2_brain_planning(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        stage1=stage1,
        console=console,  # type: ignore[arg-type]
    )

    assert result is not None
    assert result.plan_text == "Decision: keep silent now.\nAction: do not send_message."


def test_stage2_planning_prompt_includes_structured_sections():
    captured: dict[str, str] = {}

    class _Client:
        def chat(self, messages):
            last = messages[-1]
            assert last.role == "user"
            assert isinstance(last.content, str)
            captured["prompt"] = last.content
            return "Plan text"

    console = _fake_console()
    stage1 = Stage1GatheringResult(
        transcript="stage1",
        findings_text="known facts",
        tool_calls=0,
        final_response=LLMResponse(content=None, tool_calls=[]),
    )

    result = run_stage2_brain_planning(
        client=_Client(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="hi")],
        stage1=stage1,
        console=console,  # type: ignore[arg-type]
    )

    assert result is not None
    prompt = captured["prompt"]
    assert "ULTRA THINK" in prompt
    assert "[CURRENT_STATE]" in prompt
    assert "[FILE_UPDATE_PLAN]" in prompt
