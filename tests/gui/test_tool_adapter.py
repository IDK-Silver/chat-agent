"""Tests for gui/tool_adapter.py: Brain-facing gui_task / screenshot tools."""

from unittest.mock import patch

from chat_agent.gui.manager import GUITaskResult
from chat_agent.gui.tool_adapter import (
    GUI_TASK_DEFINITION,
    SCREENSHOT_DEFINITION,
    create_gui_task,
    create_screenshot,
)
from chat_agent.llm.schema import ContentPart


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


class TestScreenshotTool:
    def test_definition(self):
        assert SCREENSHOT_DEFINITION.name == "screenshot"
        assert SCREENSHOT_DEFINITION.parameters == {}
        assert SCREENSHOT_DEFINITION.required == []

    @patch("chat_agent.gui.actions.take_screenshot")
    def test_screenshot_returns_multimodal(self, mock_take):
        fake_ss = ContentPart(
            type="image", media_type="image/jpeg", data="base64data",
        )
        mock_take.return_value = fake_ss

        fn = create_screenshot(max_width=800, quality=90)
        result = fn()

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].type == "image"
        assert result[0].data == "base64data"
        assert result[1].type == "text"
        assert result[1].text == "Screenshot taken."
        mock_take.assert_called_once_with(max_width=800, quality=90)

    @patch("chat_agent.gui.actions.take_screenshot")
    def test_screenshot_error_propagates(self, mock_take):
        mock_take.side_effect = RuntimeError("No display")
        fn = create_screenshot()
        try:
            fn()
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "No display" in str(e)
