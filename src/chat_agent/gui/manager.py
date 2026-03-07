"""GUI Manager: agentic tool loop using a Pro LLM to orchestrate desktop tasks."""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..llm.base import LLMClient
from ..llm.schema import (
    ContentPart,
    Message,
    ToolCall,
    ToolDefinition,
    ToolParameter,
    make_tool_result_message,
)
from ..tools.registry import ToolRegistry, ToolResult
from .actions import (
    activate_app,
    capture_screenshot_to_temp,
    click_at_bbox,
    drag_between_bboxes,
    get_active_app,
    maximize_window,
    paste_screenshot_from_temp,
    press_key,
    right_click_at_bbox,
    scroll_at_bbox,
    take_screenshot,
    type_text,
    wait as wait_action,
)
from .worker import GUIWorker

if TYPE_CHECKING:
    from .session import GUISessionStore

logger = logging.getLogger(__name__)

_MAX_STEPS = 20
_WAIT_CANCEL_POLL_SECONDS = 0.1

_MAX_STEPS_REPORT_PROMPT = (
    "You have reached the step limit. No tools are available. "
    "Respond with TEXT ONLY (no tool calls).\n\n"
    "Write a concise situation report covering:\n"
    "1. What was accomplished so far.\n"
    "2. What remains to be done.\n"
    "3. Whether the task seems feasible with more steps, "
    "or if the current approach is fundamentally wrong.\n\n"
    "Be specific and factual. Reference the last screen state you observed."
)

# Callback: (tool_call, result, step, max_steps, step_elapsed, total_elapsed, worker_timing)
GUIStepCallback = Callable[
    [ToolCall, str, int, int, float, float, dict[str, float] | None], None,
]

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

_RIGHT_CLICK_DEF = ToolDefinition(
    name="right_click",
    description=(
        "Right-click at the center of a bounding box to open a context menu. "
        "Use for actions like 'Save image as...' in browsers."
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

_SCROLL_DEF = ToolDefinition(
    name="scroll",
    description=(
        "Scroll the mouse wheel at a specific position. "
        "Use when pagedown/pageup don't work (embedded frames, unfocused panels, "
        "custom scroll areas). Scroll amount is controlled by system config."
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
        "direction": ToolParameter(
            type="string",
            description="Scroll direction: 'up' or 'down'.",
        ),
    },
    required=["bbox", "direction"],
)

_DRAG_DEF = ToolDefinition(
    name="drag",
    description=(
        "Drag from one position to another. "
        "Use for installing apps (DMG to Applications), file management, "
        "and UI drag-and-drop. Both bboxes must come from a previous ask_worker result."
    ),
    parameters={
        "from_bbox": ToolParameter(
            type="array",
            description="Source bounding box [ymin, xmin, ymax, xmax], each 0-1000.",
            json_schema={
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
            },
        ),
        "to_bbox": ToolParameter(
            type="array",
            description="Destination bounding box [ymin, xmin, ymax, xmax], each 0-1000.",
            json_schema={
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
            },
        ),
        "duration": ToolParameter(
            type="number",
            description="Drag duration in seconds (default 0.5). Increase if drag fails.",
        ),
    },
    required=["from_bbox", "to_bbox"],
)

_MAXIMIZE_WINDOW_DEF = ToolDefinition(
    name="maximize_window",
    description=(
        "Maximize the frontmost window of an application to fill the screen. "
        "Use at the start of any task for better visibility."
    ),
    parameters={
        "app_name": ToolParameter(
            type="string",
            description="Application name (e.g. 'Firefox', 'Google Chrome').",
        ),
    },
    required=["app_name"],
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

_CAPTURE_SCREENSHOT_DEF = ToolDefinition(
    name="capture_screenshot",
    description=(
        "Capture the current screen and save it for later pasting. "
        "This does NOT put the image in the clipboard immediately. "
        "Use paste_screenshot when you are ready to paste."
    ),
    parameters={},
    required=[],
)

_PASTE_SCREENSHOT_DEF = ToolDefinition(
    name="paste_screenshot",
    description=(
        "Copy the previously captured screenshot to the clipboard. "
        "Call this after all type_text calls are done, then use "
        "key_press('command+v') to paste the image."
    ),
    parameters={},
    required=[],
)

_ACTIVATE_APP_DEF = ToolDefinition(
    name="activate_app",
    description=(
        "Open or switch to an application by name. "
        "Searches installed apps and activates the best match. "
        "If multiple apps match, returns the list — call again with a more specific name."
    ),
    parameters={
        "name": ToolParameter(
            type="string",
            description="Application name to search for (e.g. 'Terminal', 'LINE', 'Safari').",
        ),
    },
    required=["name"],
)

_WAIT_DEF = ToolDefinition(
    name="wait",
    description="Wait for a given number of seconds (0.1-10). Use after actions that trigger loading or transitions.",
    parameters={
        "seconds": ToolParameter(
            type="number",
            description="Seconds to wait.",
        ),
    },
    required=["seconds"],
)

_GET_ACTIVE_APP_DEF = ToolDefinition(
    name="get_active_app",
    description=(
        "Return the name of the currently focused application. "
        "Use this after switching apps to verify you are in the correct window."
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
        "report": ToolParameter(
            type="string",
            description="Detailed report of findings or results for the caller.",
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
        "report": ToolParameter(
            type="string",
            description="Detailed report of what was attempted before failure.",
        ),
    },
    required=["reason"],
)

_REPORT_PROBLEM_DEF = ToolDefinition(
    name="report_problem",
    description=(
        "Report an obstacle that prevents progress and return control to the caller. "
        "Use this when you cannot find a target after 2-3 attempts, "
        "encounter an unexpected state, or need different instructions. "
        "The caller may provide corrected instructions and retry."
    ),
    parameters={
        "problem": ToolParameter(
            type="string",
            description="What went wrong and what you tried.",
        ),
        "report": ToolParameter(
            type="string",
            description="Detailed context for the caller.",
        ),
    },
    required=["problem"],
)

_SCAN_LAYOUT_DEF = ToolDefinition(
    name="scan_layout",
    description=(
        "Analyze the current screen and return a structured description of the "
        "GUI layout — all visible panels, regions, toolbars, and interactive elements. "
        "Call this at the START of every task before performing any actions."
    ),
    parameters={},
    required=[],
)

MANAGER_TOOLS = [
    _SCAN_LAYOUT_DEF,
    _ASK_WORKER_DEF,
    _CLICK_DEF,
    _RIGHT_CLICK_DEF,
    _SCROLL_DEF,
    _DRAG_DEF,
    _MAXIMIZE_WINDOW_DEF,
    _TYPE_TEXT_DEF,
    _KEY_PRESS_DEF,
    _SCREENSHOT_DEF,
    _CAPTURE_SCREENSHOT_DEF,
    _PASTE_SCREENSHOT_DEF,
    _ACTIVATE_APP_DEF,
    _WAIT_DEF,
    _GET_ACTIVE_APP_DEF,
    _DONE_DEF,
    _FAIL_DEF,
    _REPORT_PROBLEM_DEF,
]


class GUITaskResult(BaseModel):
    """Result of a GUI task execution."""

    success: bool
    summary: str
    report: str = ""
    needs_input: bool = False
    session_id: str = ""
    steps_used: int
    elapsed_sec: float = 0.0
    screenshot_path: str = ""


class _LoopTermination(BaseModel):
    """Internal signal to stop the agentic loop."""

    success: bool
    summary: str
    report: str = ""
    needs_input: bool = False


class _GUICommandCancelled(Exception):
    """Raised when a GUI task is cancelled by the user."""


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
        session_store: GUISessionStore | None = None,
        on_step: GUIStepCallback | None = None,
        screenshot_max_width: int | None = None,
        screenshot_quality: int = 80,
        scroll_invert: bool = False,
        scroll_max_amount: int = 5,
        is_cancel_requested: Callable[[], bool] | None = None,
        allow_direct_screenshot: bool = False,
        allow_wait_tool: bool = True,
        step_delay_min: float = 0.0,
        step_delay_max: float = 0.0,
    ):
        self.client = client
        self.worker = worker
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.session_store = session_store
        self.on_step = on_step
        self._screenshot_max_width = screenshot_max_width
        self._screenshot_quality = screenshot_quality
        self._scroll_invert = scroll_invert
        self._scroll_max_amount = scroll_max_amount
        self._is_cancel_requested = is_cancel_requested
        self._allow_direct_screenshot = allow_direct_screenshot
        self._step_delay_min = step_delay_min
        self._step_delay_max = max(step_delay_max, step_delay_min)
        self._last_worker_timing: dict[str, float] | None = None
        self._capture_temp = os.path.join(
            tempfile.gettempdir(), f"chat_agent_capture_{os.getpid()}.png",
        )
        # Build tool list: conditionally exclude screenshot / wait
        _exclude = set()
        if not allow_direct_screenshot:
            _exclude.add("screenshot")
        if not allow_wait_tool:
            _exclude.add("wait")
        self._tools: list[ToolDefinition] = [
            t for t in MANAGER_TOOLS if t.name not in _exclude
        ]

    @property
    def capture_dir(self) -> str:
        """Directory containing GUI capture temp files."""
        return os.path.dirname(self._capture_temp)

    def execute_task(
        self,
        intent: str,
        session_id: str | None = None,
        app_prompt_text: str | None = None,
    ) -> GUITaskResult:
        """Execute a GUI task. Brain calls this once; runs full loop internally.

        If session_id is given and session_store is available, resumes from
        the previous session's recorded steps.

        If app_prompt_text is provided, it is appended to the system prompt
        as app-specific context for this execution only.
        """
        from .session import GUIStepRecord

        # Session handling
        gui_session_id = ""
        resume_context = ""
        resume_last_app = ""
        if self.session_store is not None:
            if session_id:
                session_data = self.session_store.load(session_id)
                gui_session_id = session_data.session_id
                resume_context = self.session_store.format_steps_as_context(session_data)
                resume_last_app = session_data.last_active_app
            else:
                session_data = self.session_store.create(intent)
                gui_session_id = session_data.session_id

        # Build system prompt (base + optional app-specific context)
        system_content = self.system_prompt
        if app_prompt_text:
            system_content += (
                "\n\n## App-Specific Guide\n\n" + app_prompt_text
            )

        messages = [
            Message(role="system", content=system_content),
        ]

        # Inject resume context if we have prior steps
        if resume_context:
            # Re-activate last known app
            if resume_last_app:
                try:
                    activate_app(resume_last_app)
                    time.sleep(0.5)
                except Exception:
                    logger.warning("Failed to re-activate: %s", resume_last_app)

            # Build resume message (multimodal with screenshot or text-only)
            if self._allow_direct_screenshot:
                resume_text = (
                    f"{resume_context}\n\n"
                    "You are resuming a previous task. "
                    "The screenshot shows the current screen state. "
                    "Do NOT repeat already-completed steps."
                )
                try:
                    screenshot_part = take_screenshot(
                        max_width=self._screenshot_max_width,
                        quality=self._screenshot_quality,
                    )
                    messages.append(Message(role="user", content=[
                        ContentPart(type="text", text=resume_text),
                        screenshot_part,
                        ContentPart(type="text", text=f"New instruction: {intent}"),
                    ]))
                except Exception:
                    logger.warning("Resume screenshot failed, text-only fallback")
                    messages.append(Message(
                        role="user",
                        content=(
                            f"{resume_context}\n\n"
                            "You are resuming a previous task. "
                            "Do NOT repeat already-completed steps.\n\n"
                            f"New instruction: {intent}"
                        ),
                    ))
            else:
                # Text-only resume: use worker for screen description
                resume_text = (
                    f"{resume_context}\n\n"
                    "You are resuming a previous task. "
                    "Do NOT repeat already-completed steps."
                )
                screen_desc = ""
                try:
                    screen_desc = self.worker.scan_layout()
                except Exception:
                    logger.warning("Resume scan_layout failed")
                if screen_desc:
                    resume_text += f"\n\nCurrent screen state:\n{screen_desc}"
                messages.append(Message(
                    role="user",
                    content=f"{resume_text}\n\nNew instruction: {intent}",
                ))
        else:
            messages.append(Message(
                role="user",
                content=f"GUI TASK: {intent}",
            ))

        registry = self._build_registry()

        steps = 0
        task_start = time.monotonic()
        step_start = time.monotonic()
        try:
            self._raise_if_cancel_requested()
            response = self.client.chat_with_tools(messages, self._tools)
            self._raise_if_cancel_requested()

            while response.has_tool_calls() and steps < self.max_steps:
                self._raise_if_cancel_requested()
                messages.append(Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))

                termination: _LoopTermination | None = None
                for tool_call in response.tool_calls:
                    self._raise_if_cancel_requested()
                    term = self._check_terminal(tool_call)
                    if term is not None:
                        termination = term
                        elapsed = time.monotonic() - step_start
                        total = time.monotonic() - task_start
                        self._notify_step(
                            tool_call, term.summary, steps + 1, elapsed, total,
                        )
                        messages.append(make_tool_result_message(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=term.summary,
                        ))
                        continue

                    tool_result = self._execute_tool(registry, tool_call)
                    content = tool_result.content
                    elapsed = time.monotonic() - step_start
                    total = time.monotonic() - task_start

                    # Extract worker timing (console only, not in LLM context)
                    worker_timing: dict[str, float] | None = None
                    if tool_call.name == "ask_worker" and self._last_worker_timing:
                        worker_timing = self._last_worker_timing
                        self._last_worker_timing = None

                    if isinstance(content, list):
                        result_str = "(screenshot)"
                        messages.append(make_tool_result_message(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=content,
                        ))
                    else:
                        result_str = str(content)
                        messages.append(make_tool_result_message(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=result_str,
                        ))
                    steps += 1
                    self._notify_step(
                        tool_call, result_str, steps, elapsed, total, worker_timing,
                    )
                    step_start = time.monotonic()

                    # Record step
                    if self.session_store is not None:
                        step_record = GUIStepRecord(
                            tool=tool_call.name,
                            args=tool_call.arguments,
                            result=result_str,
                        )
                        try:
                            self.session_store.append_step(gui_session_id, step_record)
                        except Exception:
                            logger.warning("Failed to record GUI step")

                    self._raise_if_cancel_requested()

                if termination is not None:
                    return self._finalize_result(
                        gui_session_id=gui_session_id,
                        task_start=task_start,
                        steps=steps,
                        success=termination.success,
                        summary=termination.summary,
                        report=termination.report,
                        needs_input=termination.needs_input,
                    )

                if self._step_delay_max > 0:
                    time.sleep(random.uniform(self._step_delay_min, self._step_delay_max))
                step_start = time.monotonic()
                self._raise_if_cancel_requested()
                response = self.client.chat_with_tools(messages, self._tools)
                self._raise_if_cancel_requested()

            # Loop ended without done/fail
            summary: str
            report = ""
            if steps >= self.max_steps:
                summary = f"Exceeded max steps ({self.max_steps})"
                report = self._request_situation_report(messages)
            else:
                summary = response.content or "Task ended without explicit completion signal."
            return self._finalize_result(
                gui_session_id=gui_session_id,
                task_start=task_start,
                steps=steps,
                success=False,
                summary=summary,
                report=report,
            )
        except _GUICommandCancelled:
            return self._finalize_result(
                gui_session_id=gui_session_id,
                task_start=task_start,
                steps=steps,
                success=False,
                summary="Cancelled by user.",
            )

    def _notify_step(
        self,
        tool_call: ToolCall,
        result: str,
        step: int,
        elapsed_sec: float,
        total_elapsed_sec: float,
        worker_timing: dict[str, float] | None = None,
    ) -> None:
        """Invoke on_step callback, swallowing any exceptions."""
        if self.on_step is None:
            return
        try:
            self.on_step(
                tool_call, result, step, self.max_steps,
                elapsed_sec, total_elapsed_sec, worker_timing,
            )
        except Exception:
            logger.warning("on_step callback failed for step %d", step)

    def _raise_if_cancel_requested(self) -> None:
        """Abort GUI loop when a user interrupt request is pending."""
        if self._is_cancel_requested is not None and self._is_cancel_requested():
            raise _GUICommandCancelled

    def _request_situation_report(self, messages: list[Message]) -> str:
        """One extra LLM call (no tools) to get a situation report on max-steps.

        The LLM has full conversation context and can summarize progress
        for the caller. Returns empty string on any failure.
        """
        try:
            self._raise_if_cancel_requested()
            messages.append(Message(
                role="user",
                content=_MAX_STEPS_REPORT_PROMPT,
            ))
            response = self.client.chat_with_tools(messages, [])
            self._raise_if_cancel_requested()
            return response.content or ""
        except _GUICommandCancelled:
            raise
        except Exception:
            logger.warning("Failed to get max-steps situation report")
            return ""

    def _finalize_result(
        self,
        *,
        gui_session_id: str,
        task_start: float,
        steps: int,
        success: bool,
        summary: str,
        report: str = "",
        needs_input: bool = False,
    ) -> GUITaskResult:
        """Finalize GUI session persistence and build a stable result object."""
        if self.session_store is not None:
            try:
                self.session_store.finalize(
                    gui_session_id,
                    success=success,
                    summary=summary,
                    report=report,
                )
            except Exception:
                logger.warning("Failed to finalize GUI session")
        capture = self._capture_temp if os.path.isfile(self._capture_temp) else ""
        return GUITaskResult(
            success=success,
            summary=summary,
            report=report,
            needs_input=needs_input,
            session_id=gui_session_id,
            steps_used=steps,
            elapsed_sec=time.monotonic() - task_start,
            screenshot_path=capture,
        )

    def _check_terminal(self, tool_call: ToolCall) -> _LoopTermination | None:
        """Check if a tool call is a termination signal (done/fail/report_problem)."""
        if tool_call.name == "done":
            return _LoopTermination(
                success=True,
                summary=tool_call.arguments.get("summary", "Task completed."),
                report=tool_call.arguments.get("report", ""),
            )
        if tool_call.name == "fail":
            return _LoopTermination(
                success=False,
                summary=tool_call.arguments.get("reason", "Task failed."),
                report=tool_call.arguments.get("report", ""),
            )
        if tool_call.name == "report_problem":
            return _LoopTermination(
                success=False,
                summary=tool_call.arguments.get("problem", "Problem reported."),
                report=tool_call.arguments.get("report", ""),
                needs_input=True,
            )
        return None

    def _execute_tool(
        self,
        registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> ToolResult:
        """Execute a non-terminal tool call, catching errors."""
        try:
            return registry.execute(tool_call)
        except _GUICommandCancelled:
            raise
        except Exception as e:
            logger.warning("GUI tool %s failed: %s", tool_call.name, e)
            return ToolResult(f"Error: {e}", is_error=True)

    def _build_registry(self) -> ToolRegistry:
        """Build internal tool registry (excludes done/fail)."""
        registry = ToolRegistry()

        # scan_layout
        def scan_layout_fn(**kwargs: Any) -> str:
            try:
                result = self.worker.scan_layout()
            except Exception as e:
                return f"Layout scan error: {e}"
            return result

        registry.register("scan_layout", scan_layout_fn, _SCAN_LAYOUT_DEF)

        # ask_worker
        def ask_worker_fn(instruction: str = "", **kwargs: Any) -> str:
            try:
                obs = self.worker.observe(instruction)
            except Exception as e:
                self._last_worker_timing = None
                return f"Worker error: {e}"
            self._last_worker_timing = {
                "screenshot": obs.screenshot_sec,
                "inference": obs.inference_sec,
            }
            parts = [obs.description]
            if obs.found and obs.bbox:
                parts.append(f"bbox: {obs.bbox}")
            elif not obs.found:
                parts.append("(target NOT found)")
            if obs.obstructed:
                parts.append(f"OBSTRUCTED: {obs.obstructed}")
            if obs.mismatch:
                parts.append(f"MISMATCH: {obs.mismatch}")
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

        # right_click
        def right_click_fn(bbox: list[int] | None = None, **kwargs: Any) -> str:
            if not bbox or len(bbox) != 4:
                return "Error: bbox must be [ymin, xmin, ymax, xmax]"
            try:
                return right_click_at_bbox(bbox)
            except Exception as e:
                return f"Right-click error: {e}"

        registry.register("right_click", right_click_fn, _RIGHT_CLICK_DEF)

        # scroll
        def scroll_fn(
            bbox: list[int] | None = None,
            direction: str = "down",
            **kwargs: Any,
        ) -> str:
            if not bbox or len(bbox) != 4:
                return "Error: bbox must be [ymin, xmin, ymax, xmax]"
            if direction not in ("up", "down"):
                return "Error: direction must be 'up' or 'down'"
            try:
                return scroll_at_bbox(
                    bbox, direction, self._scroll_max_amount,
                    invert=self._scroll_invert,
                )
            except Exception as e:
                return f"Scroll error: {e}"

        registry.register("scroll", scroll_fn, _SCROLL_DEF)

        # drag
        def drag_fn(
            from_bbox: list[int] | None = None,
            to_bbox: list[int] | None = None,
            duration: float = 0.5,
            **kwargs: Any,
        ) -> str:
            if not from_bbox or len(from_bbox) != 4:
                return "Error: from_bbox must be [ymin, xmin, ymax, xmax]"
            if not to_bbox or len(to_bbox) != 4:
                return "Error: to_bbox must be [ymin, xmin, ymax, xmax]"
            try:
                return drag_between_bboxes(from_bbox, to_bbox, duration)
            except Exception as e:
                return f"Drag error: {e}"

        registry.register("drag", drag_fn, _DRAG_DEF)

        # maximize_window
        def maximize_window_fn(app_name: str = "", **kwargs: Any) -> str:
            if not app_name:
                return "Error: app_name is required"
            try:
                return maximize_window(app_name)
            except Exception as e:
                return f"Maximize error: {e}"

        registry.register("maximize_window", maximize_window_fn, _MAXIMIZE_WINDOW_DEF)

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

        # screenshot (multimodal return) -- only when direct viewing is enabled
        if self._allow_direct_screenshot:
            def screenshot_fn(**kwargs: Any) -> list[ContentPart]:
                ss = take_screenshot(
                    max_width=self._screenshot_max_width,
                    quality=self._screenshot_quality,
                )
                return [ss, ContentPart(type="text", text="Screenshot taken.")]

            registry.register("screenshot", screenshot_fn, _SCREENSHOT_DEF)

        # capture_screenshot
        def capture_screenshot_fn(**kwargs: Any) -> str:
            return capture_screenshot_to_temp(self._capture_temp)

        registry.register("capture_screenshot", capture_screenshot_fn, _CAPTURE_SCREENSHOT_DEF)

        # paste_screenshot
        def paste_screenshot_fn(**kwargs: Any) -> str:
            return paste_screenshot_from_temp(self._capture_temp)

        registry.register("paste_screenshot", paste_screenshot_fn, _PASTE_SCREENSHOT_DEF)

        # activate_app
        def activate_app_fn(name: str = "", **kwargs: Any) -> str:
            if not name:
                return "Error: name is required"
            try:
                return activate_app(name)
            except Exception as e:
                return f"Error: {e}"

        registry.register("activate_app", activate_app_fn, _ACTIVATE_APP_DEF)

        # get_active_app
        def get_active_app_fn(**kwargs: Any) -> str:
            return get_active_app()

        registry.register("get_active_app", get_active_app_fn, _GET_ACTIVE_APP_DEF)

        # wait
        def wait_fn(seconds: float = 1.0, **kwargs: Any) -> str:
            seconds = min(max(seconds, 0.1), 10.0)
            if self._is_cancel_requested is None:
                return wait_action(seconds)
            end = time.monotonic() + seconds
            while True:
                self._raise_if_cancel_requested()
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(_WAIT_CANCEL_POLL_SECONDS, remaining))
            return f"Waited {seconds:.1f}s"

        registry.register("wait", wait_fn, _WAIT_DEF)

        return registry
