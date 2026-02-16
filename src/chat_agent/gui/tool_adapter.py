"""Brain-facing gui_task / screenshot tool definitions and factories."""

import logging
from collections.abc import Callable
from typing import Any

from ..llm.schema import ContentPart, ToolDefinition, ToolParameter
from .manager import GUIManager

logger = logging.getLogger(__name__)

GUI_TASK_DEFINITION = ToolDefinition(
    name="gui_task",
    description=(
        "Delegate a GUI task to an autonomous desktop agent. "
        "The intent is a self-contained prompt for a subagent — "
        "it must be understandable WITHOUT any conversation context. "
        "Write the intent as a GOAL, not step-by-step instructions. "
        "The GUI agent decides HOW to achieve the goal.\n"
        "\n"
        "Intent guidelines:\n"
        "- State the goal and success criteria clearly.\n"
        "- Do NOT include URLs. Describe the destination instead "
        "(e.g. 'find X's Twitter page' not 'go to twitter.com/X').\n"
        "- Do NOT include conversation context, nicknames, or "
        "references that only make sense in this chat.\n"
        "- For search tasks, provide alternative names/keywords "
        "the agent can try if the primary name is not found.\n"
        "- Include constraints (save path, app preference) as bullet points.\n"
        "\n"
        "Good: 'Download a photo of the singer Kano (鹿乃) from her "
        "Twitter/X page. Search keywords: 鹿乃, Kano, kano_hanano. "
        "Save to ~/Pictures/kano.jpg.'\n"
        "Bad: 'Go to https://twitter.com/kano_hanano and save a cute photo "
        "for 老公 to monitor API requests.'"
    ),
    parameters={
        "intent": ToolParameter(
            type="string",
            description=(
                "Self-contained goal description for the GUI subagent. "
                "Must be understandable without conversation context. "
                "Describe WHAT to achieve, not HOW to operate."
            ),
        ),
        "session_id": ToolParameter(
            type="string",
            description="Optional session ID to resume a previous GUI task.",
        ),
    },
    required=["intent"],
)


def create_gui_task(manager: GUIManager) -> Callable[..., str]:
    """Create gui_task tool function bound to a GUIManager instance."""

    def gui_task(intent: str = "", session_id: str = "", **kwargs: Any) -> str:
        if not intent:
            return "Error: intent is required."
        try:
            result = manager.execute_task(intent, session_id=session_id or None)
        except Exception as e:
            logger.error("GUI task error: %s", e)
            return f"GUI task error: {e}"
        if result.needs_input:
            status = "BLOCKED"
        elif result.success:
            status = "SUCCESS"
        else:
            status = "FAILED"
        parts = [f"[GUI {status}] (steps: {result.steps_used}, time: {result.elapsed_sec:.1f}s, session: {result.session_id})"]
        parts.append(result.summary)
        if result.screenshot_path:
            parts.append(f"\nScreenshot: {result.screenshot_path}")
        if result.report:
            parts.append(f"\nReport:\n{result.report}")
        if result.needs_input:
            parts.append("\nYou may issue a new gui_task with adjusted instructions to retry.")
        return "\n".join(parts)

    return gui_task


SCREENSHOT_DEFINITION = ToolDefinition(
    name="screenshot",
    description=(
        "Take a screenshot of the current screen and return it for visual analysis. "
        "Use this to see what is currently displayed on the desktop."
    ),
    parameters={},
    required=[],
)


def create_screenshot(
    *,
    max_width: int | None = 1280,
    quality: int = 80,
) -> Callable[..., list[ContentPart]]:
    """Create screenshot tool that returns multimodal content."""

    def screenshot(**kwargs: Any) -> list[ContentPart]:
        from .actions import take_screenshot

        ss = take_screenshot(max_width=max_width, quality=quality)
        return [ss, ContentPart(type="text", text="Screenshot taken.")]

    return screenshot
