"""Brain staged planning helpers (gather -> plan -> execute).

Stage 1: read-only tool gathering
Stage 2: pure-text planning (no schema parsing)
Stage 3: execution happens in AgentCore via the normal responder loop
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import uuid
from typing import Any

from ..llm.base import LLMClient
from ..llm.schema import ContentPart, LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from ..tools import ToolRegistry
from .ui_event_console import AgentUiPort

STAGE1_SYNTHETIC_TOOL_NAME = "_stage1_gather"

_STAGE1_USER_PROMPT = (
    "[SYSTEM] Stage 1/3: information gathering (read-only).\n"
    "Only use the provided read-only tools to search memory/files/history.\n"
    "Do not send messages. Do not modify memory.\n"
    "If prior [Stage 1 Findings] exist in conversation and remain relevant, "
    "you may reuse them and skip redundant searches.\n"
    "When you have sufficient information, stop calling tools."
)
_STAGE1_FORCE_MEMORY_SEARCH_PROMPT = (
    "[SYSTEM] Stage 1 gate: memory_search is required before any other action. "
    "Call memory_search now with a non-empty query based on the latest user message. "
    "Use concise keywords only."
)
_STAGE2_PLAN_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 2/3: planning only. Do not call tools. Do not send messages.\n"
    "Before output, perform ULTRA THINK internally to reason about current state and risks.\n"
    "Produce a complete plain-text execution plan for Stage 3:\n"
    "[CURRENT_STATE]\n"
    "- What is happening, key signals, confidence/uncertainty.\n"
    "[DECISION]\n"
    "- Act now or stay silent, and why.\n"
    "[ACTION_PLAN]\n"
    "- Exact tool actions to execute (or explicitly `none`).\n"
    "[FILE_UPDATE_PLAN]\n"
    "- Whether any files need updating.\n"
    "- For each file: path, reason, and suggested content.\n"
    "- If no file updates needed, explicitly `none`.\n"
    "[SCHEDULE_PLAN]\n"
    "- Whether to adjust schedule (add/remove/list) and why.\n"
    "[EXECUTION_RULES]\n"
    "- Constraints and guardrails for Stage 3 execution.\n\n"
    "Use the facts gathered below to decide the next actions.\n\n"
    "[Stage 1 Findings]\n{findings}\n\n"
    "Build an execution plan for Stage 3."
)
_STAGE3_EXECUTION_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 3/3: execute according to the plan below.\n"
    "Follow the plan strictly; adjust only when necessary.\n"
    "If deviating, final behavior must still align with user intent.\n\n"
    "[Stage 2 Plan]\n{plan_text}"
)
_PLAN_CONTEXT_HEADER = (
    "[SYSTEM] Planning context: "
    "prioritize these files when planning and executing."
)


@dataclass
class Stage1GatheringResult:
    transcript: str
    findings_text: str
    tool_calls: int
    final_response: LLMResponse


@dataclass
class Stage2PlanningResult:
    plan_text: str
    raw_response: str


def format_stage2_plan_for_tui(plan_text: str) -> str:
    return plan_text.strip()


def build_stage3_plan_overlay_message(plan_text: str) -> Message:
    return Message(
        role="system",
        content=_STAGE3_EXECUTION_PROMPT_TEMPLATE.format(plan_text=plan_text),
    )


def build_stage1_findings_overlay_message(findings_text: str) -> Message:
    return Message(
        role="system",
        content=f"[SYSTEM] Stage 1 findings for reference:\n{findings_text}",
    )


def build_stage1_findings_for_conversation(
    findings_text: str,
) -> tuple[ToolCall, str]:
    """Build synthetic tool call + result content for persisting findings."""
    call = ToolCall(
        id=f"stage1_{uuid.uuid4().hex[:8]}",
        name=STAGE1_SYNTHETIC_TOOL_NAME,
        arguments={},
    )
    return call, findings_text


def build_plan_context_message(files: list[tuple[str, str]]) -> Message | None:
    """Build a single system message embedding plan_context_files content.

    Each entry in *files* is (rel_path, file_content).
    Returns None when the list is empty.
    """
    if not files:
        return None
    sections = [
        f'<file path="{rel_path}">\n{content.rstrip()}\n</file>'
        for rel_path, content in files
    ]
    return Message(
        role="system",
        content=f"{_PLAN_CONTEXT_HEADER}\n" + "\n".join(sections),
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
    skip_memory_search_gate: bool = False,
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

    if skip_memory_search_gate:
        initial_memory_search_done = True
    else:
        requires_initial_memory_search = any(
            tool.name == "memory_search" for tool in stage1_tools
        )
        initial_memory_search_done = not requires_initial_memory_search

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

        if not initial_memory_search_done:
            gate_error = _validate_initial_memory_search_call(response)
            if gate_error is not None:
                lines.append(f"[stage1-gate] {gate_error}")
                local_messages.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                        reasoning_details=response.reasoning_details,
                        tool_calls=response.tool_calls,
                    )
                )
                local_messages.append(
                    Message(role="user", content=_STAGE1_FORCE_MEMORY_SEARCH_PROMPT),
                )
                if iterations >= max(1, max_iterations):
                    lines.append(f"[stage1] reached max iterations={max(1, max_iterations)}")
                    break
                continue
            initial_memory_search_done = True

        if not response.has_tool_calls():
            break
        local_messages.append(
            Message(
                role="assistant",
                content=response.content,
                reasoning_content=response.reasoning_content,
                reasoning_details=response.reasoning_details,
                tool_calls=response.tool_calls,
            ),
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
    return Stage1GatheringResult(
        transcript=transcript,
        findings_text=transcript,
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
) -> Stage2PlanningResult | None:
    user_prompt = _STAGE2_PLAN_PROMPT_TEMPLATE.format(findings=stage1.findings_text)

    if raise_if_cancel_requested is not None:
        raise_if_cancel_requested()
    with console.spinner("Stage 2/3: planning..."):
        raw = client.chat([*messages, Message(role="user", content=user_prompt)])
    if raise_if_cancel_requested is not None:
        raise_if_cancel_requested()

    plan_text = (raw or "").strip()
    if plan_text:
        return Stage2PlanningResult(plan_text=plan_text, raw_response=raw or "")

    if console.debug:
        console.print_debug("staged-plan", "stage2 planning failed: empty response")
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
        return "\n".join(text_parts).strip() or "(multimodal tool result)"
    return str(result).strip()


def _validate_initial_memory_search_call(response: LLMResponse) -> str | None:
    if not response.has_tool_calls():
        return "missing required initial memory_search tool call."
    first = response.tool_calls[0]
    if first.name != "memory_search":
        return "first tool call must be memory_search."
    query = _extract_memory_search_query(first.arguments)
    if not isinstance(query, str) or not query.strip():
        return "initial memory_search query must be non-empty."
    return None


def _extract_memory_search_query(arguments: dict[str, Any]) -> Any:
    return arguments.get("query") or arguments.get("q") or arguments.get("search")
