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
    SCREENSHOT_DEFINITION,
    create_gui_task,
    create_screenshot,
)
from .worker import GUIWorker, WorkerObservation

__all__ = [
    "GUI_TASK_DEFINITION",
    "GUIManager",
    "GUIStepCallback",
    "GUISessionData",
    "GUISessionStore",
    "GUIStepRecord",
    "GUITaskResult",
    "GUIWorker",
    "SCREENSHOT_DEFINITION",
    "WorkerObservation",
    "create_gui_task",
    "create_screenshot",
]
