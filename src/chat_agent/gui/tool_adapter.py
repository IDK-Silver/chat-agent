"""Brain-facing gui_task tool definition and factory."""

import logging
from collections.abc import Callable
from typing import Any

from ..llm.schema import ToolDefinition, ToolParameter
from .manager import GUIManager

logger = logging.getLogger(__name__)

GUI_TASK_DEFINITION = ToolDefinition(
    name="gui_task",
    description=(
        "Execute a desktop GUI automation task. "
        "Provide a high-level intent describing what you want to accomplish "
        "on the macOS desktop. The GUI agent will take screenshots, "
        "find UI elements, click, type, and verify results autonomously. "
        "Examples: 'Open Safari and go to google.com', "
        "'Send a message saying good morning in the Line app', "
        "'Take a screenshot and describe what is on screen', "
        "'Open Finder and navigate to the Documents folder'."
    ),
    parameters={
        "intent": ToolParameter(
            type="string",
            description="High-level description of the GUI task to perform.",
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
        status = "SUCCESS" if result.success else "FAILED"
        parts = [f"[GUI {status}] (steps: {result.steps_used}, time: {result.elapsed_sec:.1f}s, session: {result.session_id})"]
        parts.append(result.summary)
        if result.report:
            parts.append(f"\nReport:\n{result.report}")
        return "\n".join(parts)

    return gui_task
