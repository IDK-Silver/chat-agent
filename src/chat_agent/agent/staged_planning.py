"""Copilot brain staged planning helpers (see -> think -> act).

Stage 1: read-only tool gathering
Stage 2: pure-text planning (no schema parsing)
Stage 3: execution happens in AgentCore via the normal responder loop
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from ..llm.base import LLMClient
from ..llm.schema import ContentPart, LLMResponse, Message, ToolCall, ToolDefinition, ToolParameter
from ..tools import ToolRegistry
from .ui_event_console import AgentUiPort

_STAGE1_USER_PROMPT = (
    "[SYSTEM] Stage 1/3: 僅做資訊蒐集。"
    " 只能使用提供的唯讀工具搜尋相關記憶/檔案/歷史。"
    " 第一步先呼叫一次 memory_search，query 必須非空白，"
    " 並以最新使用者訊息為依據。"
    " 不可傳送訊息。不可修改記憶。"
    " 資訊足夠時結束。"
)
_STAGE1_FORCE_MEMORY_SEARCH_PROMPT = (
    "[SYSTEM] Stage 1 gate: 在任何其他行動前，memory_search 為必要。"
    " 請現在立即呼叫 memory_search，"
    " 使用依據最新使用者訊息的非空白 query。"
    " 請僅使用簡潔關鍵字。"
)
_STAGE2_PLAN_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 2/3: 僅做規劃。不可呼叫工具。不可傳送訊息。\n"
    "輸出前，請先在內部執行 ULTRA THINK，完整推理當前狀態與風險。\n"
    "請依下列結構產生 Stage 3 的完整純文字執行計畫：\n"
    "[CURRENT_STATE]\n"
    "- 當前發生什麼事，關鍵訊號，信心/不確定性。\n"
    "[DECISION]\n"
    "- 應該立即行動還是保持沉默，理由是什麼。\n"
    "[ACTION_PLAN]\n"
    "- 現在要執行的精確工具動作（或明確寫 `none`）。\n"
    "[FILE_UPDATE_PLAN]\n"
    "- 是否需要更新任何檔案。\n"
    "- 對每個檔案提供：path、reason 與建議寫入/更新內容。\n"
    "- 若不需要更新檔案，明確寫 `none`。\n"
    "[SCHEDULE_PLAN]\n"
    "- 是否需要調整排程（add/remove/list）及理由。\n"
    "[EXECUTION_RULES]\n"
    "- Stage 3 執行要遵循的限制與護欄。\n\n"
    "請使用下方蒐集到的事實來決定接下來的行動。\n\n"
    "[Stage 1 Findings]\n{findings}\n\n"
    "請為 Stage 3 建立執行計畫。"
)
_STAGE3_EXECUTION_PROMPT_TEMPLATE = (
    "[SYSTEM] Stage 3/3: 依照下列計畫執行。\n"
    "請盡量嚴格遵循計畫，只在必要時進行小幅調整。\n"
    "若有偏離，最終行為仍必須與使用者意圖一致。\n\n"
    "[Stage 2 Plan]\n{plan_text}"
)
_STAGE2_LONG_TERM_ANCHOR_HEADER = (
    "[SYSTEM] Stage 2 長期記憶錨點："
    "規劃時請優先依據這些持續性使用者規則。"
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


@dataclass
class Stage2PlanningResult:
    plan_text: str
    raw_response: str


def format_stage2_plan_for_tui(plan_text: str) -> str:
    text = plan_text.strip()
    if len(text) <= _TUI_PLAN_MAX_CHARS:
        return text
    return text[:_TUI_PLAN_MAX_CHARS] + "\n...[truncated]"


def build_stage3_plan_overlay_message(plan_text: str) -> Message:
    return Message(
        role="system",
        content=_STAGE3_EXECUTION_PROMPT_TEMPLATE.format(plan_text=plan_text),
    )


def build_stage2_long_term_anchor_message(*, rel_path: str, content: str) -> Message:
    body = content.rstrip()
    return Message(
        role="system",
        content=(
            f"{_STAGE2_LONG_TERM_ANCHOR_HEADER}\n"
            f'<file path="{rel_path}">\n{body}\n</file>'
        ),
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
    requires_initial_memory_search = any(tool.name == "memory_search" for tool in stage1_tools)
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
        preview = "\n".join(text_parts).strip() or "(multimodal tool result)"
    else:
        preview = str(result).strip()
    if len(preview) > _STAGE1_RESULT_PREVIEW_CHARS:
        preview = preview[:_STAGE1_RESULT_PREVIEW_CHARS] + "...[truncated]"
    return preview


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
