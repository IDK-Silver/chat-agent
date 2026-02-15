"""Tests for gui/tool_adapter.py: Brain-facing gui_task tool."""

from chat_agent.gui.manager import GUITaskResult
from chat_agent.gui.tool_adapter import GUI_TASK_DEFINITION, create_gui_task


class FakeManager:
    """Manager that returns a fixed result."""

    def __init__(self, result: GUITaskResult):
        self._result = result
        self.last_intent: str | None = None
        self.last_session_id: str | None = None

    def execute_task(self, intent: str, session_id: str | None = None) -> GUITaskResult:
        self.last_intent = intent
        self.last_session_id = session_id
        return self._result


class FakeErrorManager:
    """Manager that raises an exception."""

    def execute_task(self, intent: str, session_id: str | None = None) -> GUITaskResult:
        raise RuntimeError("LLM unavailable")


class TestGuiTaskDefinition:
    def test_name_and_params(self):
        assert GUI_TASK_DEFINITION.name == "gui_task"
        assert "intent" in GUI_TASK_DEFINITION.parameters
        assert "session_id" in GUI_TASK_DEFINITION.parameters
        assert GUI_TASK_DEFINITION.required == ["intent"]


class TestCreateGuiTask:
    def test_success_result(self):
        result = GUITaskResult(
            success=True, summary="Opened Finder.", steps_used=3, session_id="20260215_120000_abc123",
        )
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="Open Finder")
        assert "[GUI SUCCESS]" in output
        assert "steps: 3" in output
        assert "session: 20260215_120000_abc123" in output
        assert "Opened Finder" in output
        assert manager.last_intent == "Open Finder"

    def test_failure_result(self):
        result = GUITaskResult(success=False, summary="App not found.", steps_used=5, session_id="s1")
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

    def test_report_included_in_output(self):
        result = GUITaskResult(
            success=True, summary="Done.", report="Found 3 items.", steps_used=2, session_id="s2",
        )
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="Check screen")
        assert "Report:" in output
        assert "Found 3 items." in output

    def test_no_report_no_report_section(self):
        result = GUITaskResult(success=True, summary="Done.", steps_used=1, session_id="s3")
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        output = fn(intent="Do task")
        assert "Report:" not in output

    def test_session_id_passed_to_manager(self):
        result = GUITaskResult(success=True, summary="Done.", steps_used=0, session_id="s4")
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        fn(intent="Resume task", session_id="existing_session")
        assert manager.last_session_id == "existing_session"

    def test_empty_session_id_passed_as_none(self):
        result = GUITaskResult(success=True, summary="Done.", steps_used=0, session_id="s5")
        manager = FakeManager(result)
        fn = create_gui_task(manager)
        fn(intent="New task", session_id="")
        assert manager.last_session_id is None
