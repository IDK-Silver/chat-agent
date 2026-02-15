"""Tests for gui/tool_adapter.py: Brain-facing gui_task tool."""

from chat_agent.gui.manager import GUITaskResult
from chat_agent.gui.tool_adapter import GUI_TASK_DEFINITION, create_gui_task


class FakeManager:
    """Manager that returns a fixed result."""

    def __init__(self, result: GUITaskResult):
        self._result = result
        self.last_intent: str | None = None

    def execute_task(self, intent: str) -> GUITaskResult:
        self.last_intent = intent
        return self._result


class FakeErrorManager:
    """Manager that raises an exception."""

    def execute_task(self, intent: str) -> GUITaskResult:
        raise RuntimeError("LLM unavailable")


class TestGuiTaskDefinition:
    def test_name_and_params(self):
        assert GUI_TASK_DEFINITION.name == "gui_task"
        assert "intent" in GUI_TASK_DEFINITION.parameters
        assert GUI_TASK_DEFINITION.required == ["intent"]


class TestCreateGuiTask:
    def test_success_result(self):
        result = GUITaskResult(success=True, summary="Opened Finder.", steps_used=3)
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="Open Finder")
        assert "[GUI SUCCESS]" in output
        assert "steps: 3" in output
        assert "Opened Finder" in output
        assert manager.last_intent == "Open Finder"

    def test_failure_result(self):
        result = GUITaskResult(success=False, summary="App not found.", steps_used=5)
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="Open nonexistent app")
        assert "[GUI FAILED]" in output
        assert "App not found" in output

    def test_empty_intent_error(self):
        result = GUITaskResult(success=True, summary="ok", steps_used=0)
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="")
        assert "Error" in output

    def test_exception_handled(self):
        manager = FakeErrorManager()
        fn = create_gui_task(manager)
        output = fn(intent="Do something")
        assert "error" in output.lower()
        assert "LLM unavailable" in output
