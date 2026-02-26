"""Copilot brain staged planning helpers (see -> think -> act).

Stage 1: read-only tool gathering
Stage 2: pure-text planning with JSON output
Stage 3: execution happens in AgentCore via the normal responder loop
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..llm.json_extract import extract_json_object
from ..llm.schema import ContentPart, LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from ..tools import ToolRegistry
from .ui_event_console import AgentUiPort

_STAGE1_USER_PROMPT = (
    "[SYSTEM] Stage 1/3: Gather facts only. "
    "Use only the provided read-only tools to collect relevant memory/files/history. "
    "Do not send messages. Do not modify memory. "
    "Stop when you have enough information."
)
_STAGE2_PLAN_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 2/3: Planning only. Do NOT call tools. Do NOT send messages.\n"
    "You must produce ONLY JSON matching the schema.\n\n"
    "Use the gathered facts below to decide what to do.\n\n"
    "[Stage 1 Findings]\n{findings}\n\n"
    "Create an execution plan for Stage 3."
)
_STAGE2_PARSE_RETRY_PROMPT = (
    "[SYSTEM] Your previous output did not match the required JSON schema. "
    "Return only valid JSON that matches the schema. No markdown."
)
_STAGE3_EXECUTION_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 3/3: Execute according to the plan below.\n"
    "Follow the plan closely. Small adjustments are allowed only when necessary.\n"
    "If you deviate, briefly explain in tool usage reasoning text (if any).\n\n"
    "[Stage 2 Plan]\n{plan_json}"
)
_STAGE1_RESULT_PREVIEW_CHARS = 2000
_STAGE1_FINDINGS_MAX_CHARS = 12000
_TUI_PLAN_MAX_CHARS = 12000


@dataclass
class Stage1GatheringResult:
    transcript: str
    findings_text: str
    tool_calls: int
    final_response: LLMResponse


class PlanAction(BaseModel):
    tool: str
    purpose: str
    required: bool
    max_calls: int | None = Field(default=None, ge=1)
    arguments_hint: dict[str, Any] | None = None


class Stage2BrainPlan(BaseModel):
    version: int = 1
    summary: str
    facts: list[str] = Field(default_factory=list)
    intent_assessment: str
    planned_actions: list[PlanAction] = Field(default_factory=list)
    execution_rules: list[str] = Field(default_factory=list)
    notes_for_execution: str | None = None


@dataclass
class Stage2PlanningResult:
    plan: Stage2BrainPlan
    raw_response: str


def stage2_plan_response_schema() -> dict[str, Any]:
    return Stage2BrainPlan.model_json_schema()


def format_stage2_plan_for_tui(plan: Stage2BrainPlan) -> str:
    text = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if len(text) <= _TUI_PLAN_MAX_CHARS:
        return text
    return text[:_TUI_PLAN_MAX_CHARS] + "\n...[truncated]"


def build_stage3_plan_overlay_message(plan: Stage2BrainPlan) -> Message:
    plan_json = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return Message(
        role="system",
        content=_STAGE3_EXECUTION_PROMPT_TEMPLATE.format(plan_json=plan_json),
    )


class PlanExecutionTracker:
    """Track stage-3 deviations from the stage-2 plan."""

    def __init__(self, plan: Stage2BrainPlan, console: AgentUiPort):
        self._console = console
        self._counts: dict[str, int] = {}
        self._required_tools = {
            action.tool for action in plan.planned_actions if action.required and action.tool
        }
        self._allowed_tools = {
            action.tool for action in plan.planned_actions if action.tool
        }
        self._max_calls_by_tool: dict[str, int | None] = {}
        for action in plan.planned_actions:
            if not action.tool:
                continue
            prev = self._max_calls_by_tool.get(action.tool)
            if prev is None:
                if action.tool in self._max_calls_by_tool:
                    continue
                self._max_calls_by_tool[action.tool] = action.max_calls
                continue
            if action.max_calls is None:
                self._max_calls_by_tool[action.tool] = None
            else:
                self._max_calls_by_tool[action.tool] = prev + action.max_calls

    def on_before_tool_call(self, tool_call: ToolCall) -> None:
        name = tool_call.name
        self._counts[name] = self._counts.get(name, 0) + 1
        count = self._counts[name]
        if self._allowed_tools and name not in self._allowed_tools:
            self._console.print_warning(
                f"[plan-deviation] unplanned tool call: {name}",
                indent=2,
            )
            return
        max_calls = self._max_calls_by_tool.get(name)
        if max_calls is not None and count > max_calls:
            self._console.print_warning(
                f"[plan-deviation] tool {name} exceeded planned max_calls "
                f"({count}>{max_calls})",
                indent=2,
            )

    def finalize(self) -> None:
        missing = sorted(
            tool for tool in self._required_tools
            if self._counts.get(tool, 0) <= 0
        )
        if not missing:
            return
        self._console.print_warning(
            "[plan-deviation] required planned tools not used: " + ", ".join(missing),
            indent=2,
        )


class _Stage1RegistryProxy:
    """Read-only execution proxy for Stage 1 tool calls."""

    def __init__(self, base_registry: ToolRegistry, allowed_tool_names: set[str]):
        self._base = base_registry
        self._allowed = allowed_tool_names

    def has_tool(self, name: str) -> bool:
        return name in self._allowed and self._base.has_tool(name)

    def execute(self, tool_call: ToolCall) -> str | list[ContentPart]:
        if tool_call.name not in self._allowed:
            return f"Error: tool '{tool_call.name}' is not allowed in Stage 1"
        if tool_call.name == "schedule_action":
            action = tool_call.arguments.get("action")
            if action != "list":
                return "Error: Stage 1 schedule_action only supports action='list'"
        return self._base.execute(tool_call)


def build_stage1_tools(all_tools: list[ToolDefinition]) -> list[ToolDefinition]:
    by_name = {tool.name: tool for tool in all_tools}
    names = ["memory_search", "read_file", "get_channel_history"]
    selected: list[ToolDefinition] = []
    for name in names:
        tool = by_name.get(name)
        if tool is not None:
            selected.append(tool)

    if "schedule_action" in by_name:
        selected.append(_schedule_action_list_only_definition(by_name["schedule_action"]))
    return selected


def run_stage1_information_gathering(
    *,
    client: LLMClient,
    messages: list[Message],
    all_tools: list[ToolDefinition],
    registry: ToolRegistry,
    console: AgentUiPort,
    raise_if_cancel_requested: Callable[[], None] | None = None,
    max_iterations: int = 4,
) -> Stage1GatheringResult:
    stage1_tools = build_stage1_tools(all_tools)
    if not stage1_tools:
        return Stage1GatheringResult(
            transcript="(no stage1 tools available)",
            findings_text="(no stage1 tools available)",
            tool_calls=0,
            final_response=LLMResponse(content=None, tool_calls=[]),
        )

    local_messages = [*messages, Message(role="user", content=_STAGE1_USER_PROMPT)]
    proxy = _Stage1RegistryProxy(
        registry,
        allowed_tool_names={tool.name for tool in stage1_tools},
    )
    lines: list[str] = []
    total_tool_calls = 0
    iterations = 0
    response = LLMResponse(content=None, tool_calls=[])

    while True:
        if raise_if_cancel_requested is not None:
            raise_if_cancel_requested()
        with console.spinner("Stage 1/3: gathering..."):
            response = client.chat_with_tools(local_messages, stage1_tools)
        if raise_if_cancel_requested is not None:
            raise_if_cancel_requested()
        iterations += 1
        if response.content and response.content.strip():
            lines.append("[assistant]")
            lines.append(response.content.strip())
        if not response.has_tool_calls():
            break
        local_messages.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls),
        )
        for tool_call in response.tool_calls:
            total_tool_calls += 1
            console.print_tool_call(tool_call)
            result = proxy.execute(tool_call)
            console.print_tool_result(tool_call, result)
            result_preview = _result_to_preview_text(result)
            lines.append(f"[tool_call] {tool_call.name} {json.dumps(tool_call.arguments, ensure_ascii=False)}")
            lines.append(f"[tool_result] {result_preview}")
            local_messages.append(
                Message(
                    role="tool",
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=result,
                )
            )
        if iterations >= max(1, max_iterations):
            lines.append(f"[stage1] reached max iterations={max(1, max_iterations)}")
            break

    transcript = "\n".join(lines).strip() or "(no stage1 transcript)"
    findings = transcript
    if len(findings) > _STAGE1_FINDINGS_MAX_CHARS:
        findings = findings[:_STAGE1_FINDINGS_MAX_CHARS] + "\n...[truncated]"
    return Stage1GatheringResult(
        transcript=transcript,
        findings_text=findings,
        tool_calls=total_tool_calls,
        final_response=response,
    )


def run_stage2_brain_planning(
    *,
    client: LLMClient,
    messages: list[Message],
    stage1: Stage1GatheringResult,
    console: AgentUiPort,
    raise_if_cancel_requested: Callable[[], None] | None = None,
    parse_retries: int = 1,
) -> Stage2PlanningResult | None:
    base_messages = list(messages)
    user_prompt = _STAGE2_PLAN_PROMPT_TEMPLATE.format(findings=stage1.findings_text)
    schema = stage2_plan_response_schema()

    for attempt in range(parse_retries + 1):
        if raise_if_cancel_requested is not None:
            raise_if_cancel_requested()
        with console.spinner("Stage 2/3: planning..."):
            if attempt == 0:
                raw = client.chat(
                    [*base_messages, Message(role="user", content=user_prompt)],
                    response_schema=schema,
                )
            else:
                raw = client.chat(
                    [
                        *base_messages,
                        Message(role="user", content=user_prompt),
                        Message(role="user", content=_STAGE2_PARSE_RETRY_PROMPT),
                    ],
                    response_schema=schema,
                )
        if raise_if_cancel_requested is not None:
            raise_if_cancel_requested()
        data = extract_json_object(raw or "")
        if data is None:
            continue
        try:
            plan = Stage2BrainPlan.model_validate(data)
            return Stage2PlanningResult(plan=plan, raw_response=raw)
        except Exception:
            continue
    return None


def _schedule_action_list_only_definition(source: ToolDefinition) -> ToolDefinition:
    return ToolDefinition(
        name=source.name,
        description=(
            "Read-only list of pending scheduled actions. "
            "Stage 1 only supports action='list'."
        ),
        parameters={
            "action": ToolParameter(
                type="string",
                description="Must be 'list' in Stage 1.",
                enum=["list"],
            ),
        },
        required=["action"],
    )


def _result_to_preview_text(result: str | list[ContentPart]) -> str:
    if isinstance(result, list):
        text_parts = [
            part.text for part in result
            if part.type == "text" and part.text
        ]
        preview = "\n".join(text_parts).strip() or "(multimodal tool result)"
    else:
        preview = str(result).strip()
    if len(preview) > _STAGE1_RESULT_PREVIEW_CHARS:
        preview = preview[:_STAGE1_RESULT_PREVIEW_CHARS] + "...[truncated]"
    return preview
