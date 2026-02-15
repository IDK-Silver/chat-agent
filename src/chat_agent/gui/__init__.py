"""GUI desktop automation module.

Three-layer architecture:
- Brain calls gui_task (tool_adapter.py)
- GUIManager runs agentic tool loop (manager.py)
- GUIWorker does single-shot screenshot analysis (worker.py)
"""

from .manager import GUIManager, GUITaskResult
from .tool_adapter import GUI_TASK_DEFINITION, create_gui_task
from .worker import GUIWorker, WorkerObservation

__all__ = [
    "GUI_TASK_DEFINITION",
    "GUIManager",
    "GUITaskResult",
    "GUIWorker",
    "WorkerObservation",
    "create_gui_task",
]
