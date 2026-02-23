"""GUI desktop automation module.

Three-layer architecture:
- Brain calls gui_task (tool_adapter.py)
- GUIManager runs agentic tool loop (manager.py)
- GUIWorker does single-shot screenshot analysis (worker.py)
"""

from .manager import GUIManager, GUIStepCallback, GUITaskResult
from .session import GUISessionData, GUISessionStore, GUIStepRecord
from .tool_adapter import (
    GUI_TASK_DEFINITION,
    SCREENSHOT_BY_SUBAGENT_DEFINITION,
    SCREENSHOT_DEFINITION,
    create_gui_task,
    create_screenshot,
    create_screenshot_by_subagent,
)
from .worker import GUIWorker, ScreenDescription, WorkerObservation

__all__ = [
    "GUI_TASK_DEFINITION",
    "GUIManager",
    "GUIStepCallback",
    "GUISessionData",
    "GUISessionStore",
    "GUIStepRecord",
    "GUITaskResult",
    "GUIWorker",
    "SCREENSHOT_BY_SUBAGENT_DEFINITION",
    "SCREENSHOT_DEFINITION",
    "ScreenDescription",
    "WorkerObservation",
    "create_gui_task",
    "create_screenshot",
    "create_screenshot_by_subagent",
]
