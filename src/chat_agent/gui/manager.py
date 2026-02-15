"""GUI Manager: agentic tool loop using a Pro LLM to orchestrate desktop tasks."""

import logging
from typing import Any

from pydantic import BaseModel

from ..llm.base import LLMClient
from ..llm.schema import (
    ContentPart,
    Message,
    ToolCall,
    ToolDefinition,
    ToolParameter,
)
from ..tools.registry import ToolRegistry
from .actions import click_at_bbox, press_key, take_screenshot, type_text
from .worker import GUIWorker

logger = logging.getLogger(__name__)

_MAX_STEPS = 20

# --- Manager tool definitions ---

_ASK_WORKER_DEF = ToolDefinition(
    name="ask_worker",
    description=(
        "Ask the vision worker to take a screenshot and analyze it. "
        "Provide a specific instruction like 'Find the Send button' or "
        "'Describe what is on screen'. Returns text summary + bounding box."
    ),
    parameters={
        "instruction": ToolParameter(
            type="string",
            description="What to look for or describe in the current screenshot.",
        ),
    },
    required=["instruction"],
)

_CLICK_DEF = ToolDefinition(
    name="click",
    description=(
        "Click at the center of a bounding box. "
        "The bbox must come from a previous ask_worker result."
    ),
    parameters={
        "bbox": ToolParameter(
            type="array",
            description="Gemini bounding box [ymin, xmin, ymax, xmax], each 0-1000.",
            json_schema={
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
            },
        ),
    },
    required=["bbox"],
)

_TYPE_TEXT_DEF = ToolDefinition(
    name="type_text",
    description="Type text at the current cursor position. Supports Unicode.",
    parameters={
        "text": ToolParameter(
            type="string",
            description="The text to type.",
        ),
    },
    required=["text"],
)

_KEY_PRESS_DEF = ToolDefinition(
    name="key_press",
    description=(
        "Press a key or key combination. "
        "Examples: 'enter', 'tab', 'escape', 'command+a', 'command+v'."
    ),
    parameters={
        "key": ToolParameter(
            type="string",
            description="Key name or combo with '+' separator.",
        ),
    },
    required=["key"],
)

_SCREENSHOT_DEF = ToolDefinition(
    name="screenshot",
    description=(
        "Take a screenshot and view it directly. "
        "Use this when you need to see the screen yourself "
        "rather than relying on the worker's text summary."
    ),
    parameters={},
    required=[],
)

_DONE_DEF = ToolDefinition(
    name="done",
    description="Signal that the GUI task has been completed successfully.",
    parameters={
        "summary": ToolParameter(
            type="string",
            description="Brief summary of what was accomplished.",
        ),
    },
    required=["summary"],
)

_FAIL_DEF = ToolDefinition(
    name="fail",
    description="Signal that the GUI task could not be completed.",
    parameters={
        "reason": ToolParameter(
            type="string",
            description="Why the task failed.",
        ),
    },
    required=["reason"],
)

MANAGER_TOOLS = [
    _ASK_WORKER_DEF,
    _CLICK_DEF,
    _TYPE_TEXT_DEF,
    _KEY_PRESS_DEF,
    _SCREENSHOT_DEF,
    _DONE_DEF,
    _FAIL_DEF,
]


class GUITaskResult(BaseModel):
    """Result of a GUI task execution."""

    success: bool
    summary: str
    steps_used: int


class _LoopTermination(BaseModel):
    """Internal signal to stop the agentic loop."""

    success: bool
    summary: str


class GUIManager:
    """Orchestrates GUI automation via an agentic tool loop.

    The Manager LLM decides which tools to call (ask_worker, click, etc.)
    and loops until done/fail or max_steps is reached.
    """

    def __init__(
        self,
        client: LLMClient,
        worker: GUIWorker,
        system_prompt: str,
        max_steps: int = _MAX_STEPS,
    ):
        self.client = client
        self.worker = worker
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def execute_task(self, intent: str) -> GUITaskResult:
        """Execute a GUI task. Brain calls this once; runs full loop internally."""
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=f"GUI TASK: {intent}"),
        ]
        registry = self._build_registry()

        steps = 0
        response = self.client.chat_with_tools(messages, MANAGER_TOOLS)

        while response.has_tool_calls() and steps < self.max_steps:
            # Add assistant message with tool calls
            messages.append(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            termination: _LoopTermination | None = None
            for tool_call in response.tool_calls:
                term = self._check_terminal(tool_call)
                if term is not None:
                    termination = term
                    # Still add a tool result so the message sequence is valid
                    messages.append(Message(
                        role="tool",
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=term.summary,
                    ))
                    continue

                result = self._execute_tool(registry, tool_call)
                if isinstance(result, list):
                    # Multimodal result (screenshot)
                    messages.append(Message(
                        role="tool",
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=result,
                    ))
                else:
                    messages.append(Message(
                        role="tool",
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=str(result),
                    ))
                steps += 1

            if termination is not None:
                return GUITaskResult(
                    success=termination.success,
                    summary=termination.summary,
                    steps_used=steps,
                )

            response = self.client.chat_with_tools(messages, MANAGER_TOOLS)

        # Loop ended without done/fail
        if steps >= self.max_steps:
            return GUITaskResult(
                success=False,
                summary=f"Exceeded max steps ({self.max_steps})",
                steps_used=steps,
            )

        # LLM stopped calling tools without done/fail
        final_text = response.content or "Task ended without explicit completion signal."
        return GUITaskResult(
            success=False,
            summary=final_text,
            steps_used=steps,
        )

    def _check_terminal(self, tool_call: ToolCall) -> _LoopTermination | None:
        """Check if a tool call is a termination signal (done/fail)."""
        if tool_call.name == "done":
            return _LoopTermination(
                success=True,
                summary=tool_call.arguments.get("summary", "Task completed."),
            )
        if tool_call.name == "fail":
            return _LoopTermination(
                success=False,
                summary=tool_call.arguments.get("reason", "Task failed."),
            )
        return None

    def _execute_tool(
        self,
        registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> str | list[ContentPart]:
        """Execute a non-terminal tool call, catching errors."""
        try:
            return registry.execute(tool_call)
        except Exception as e:
            logger.warning("GUI tool %s failed: %s", tool_call.name, e)
            return f"Error: {e}"

    def _build_registry(self) -> ToolRegistry:
        """Build internal tool registry (excludes done/fail)."""
        registry = ToolRegistry()

        # ask_worker
        def ask_worker_fn(instruction: str = "", **kwargs: Any) -> str:
            try:
                obs = self.worker.observe(instruction)
            except Exception as e:
                return f"Worker error: {e}"
            parts = [obs.description]
            if obs.found and obs.bbox:
                parts.append(f"bbox: {obs.bbox}")
            elif not obs.found:
                parts.append("(target NOT found)")
            return "\n".join(parts)

        registry.register("ask_worker", ask_worker_fn, _ASK_WORKER_DEF)

        # click
        def click_fn(bbox: list[int] | None = None, **kwargs: Any) -> str:
            if not bbox or len(bbox) != 4:
                return "Error: bbox must be [ymin, xmin, ymax, xmax]"
            try:
                return click_at_bbox(bbox)
            except Exception as e:
                return f"Click error: {e}"

        registry.register("click", click_fn, _CLICK_DEF)

        # type_text
        def type_text_fn(text: str = "", **kwargs: Any) -> str:
            if not text:
                return "Error: text is required"
            try:
                return type_text(text)
            except Exception as e:
                return f"Type error: {e}"

        registry.register("type_text", type_text_fn, _TYPE_TEXT_DEF)

        # key_press
        def key_press_fn(key: str = "", **kwargs: Any) -> str:
            if not key:
                return "Error: key is required"
            try:
                return press_key(key)
            except Exception as e:
                return f"Key press error: {e}"

        registry.register("key_press", key_press_fn, _KEY_PRESS_DEF)

        # screenshot (multimodal return)
        def screenshot_fn(**kwargs: Any) -> list[ContentPart]:
            ss = take_screenshot()
            return [ss, ContentPart(type="text", text="Screenshot taken.")]

        registry.register("screenshot", screenshot_fn, _SCREENSHOT_DEF)

        return registry
