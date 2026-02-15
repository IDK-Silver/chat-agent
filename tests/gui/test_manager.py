"""Tests for gui/manager.py: GUIManager agentic loop."""

from unittest.mock import patch

from chat_agent.gui.manager import GUIManager, GUITaskResult, MANAGER_TOOLS
from chat_agent.gui.worker import GUIWorker, WorkerObservation
from chat_agent.llm.schema import ContentPart, LLMResponse, ToolCall


class FakeManagerClient:
    """LLM client that returns a sequence of LLMResponse objects."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self._idx = 0

    def chat(self, messages, response_schema=None):
        raise NotImplementedError

    def chat_with_tools(self, messages, tools):
        if self._idx >= len(self._responses):
            return LLMResponse(content="No more responses.")
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class FakeWorker:
    """Worker that returns a fixed observation."""

    def __init__(self, obs: WorkerObservation):
        self._obs = obs
        self.call_count = 0

    def observe(self, instruction: str) -> WorkerObservation:
        self.call_count += 1
        return self._obs


class TestGUIManagerDoneFail:
    def test_done_returns_success(self):
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Task completed."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Open Finder")
        assert result.success is True
        assert "completed" in result.summary

    def test_fail_returns_failure(self):
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="fail", arguments={"reason": "Could not find app."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Open something")
        assert result.success is False
        assert "Could not find" in result.summary


class TestGUIManagerTools:
    @patch("chat_agent.gui.manager.take_screenshot")
    @patch("chat_agent.gui.manager.click_at_bbox", return_value="Clicked at (100, 200)")
    def test_ask_worker_then_click_then_done(self, mock_click, mock_ss):
        mock_ss.return_value = ContentPart(
            type="image", media_type="image/png", data="fake", width=100, height=50,
        )
        obs = WorkerObservation(description="Found button", found=True, bbox=[10, 20, 30, 40])
        worker = FakeWorker(obs)

        responses = [
            # Step 1: ask_worker
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Find button"}),
            ]),
            # Step 2: click
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="click", arguments={"bbox": [10, 20, 30, 40]}),
            ]),
            # Step 3: done
            LLMResponse(tool_calls=[
                ToolCall(id="3", name="done", arguments={"summary": "Clicked the button."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Click the button")
        assert result.success is True
        assert result.steps_used == 2  # ask_worker + click (done doesn't count)
        assert worker.call_count == 1

    @patch("chat_agent.gui.manager.type_text", return_value="Typed: 'hello'")
    @patch("chat_agent.gui.manager.take_screenshot")
    def test_type_text_tool(self, mock_ss, mock_type):
        mock_ss.return_value = ContentPart(
            type="image", media_type="image/png", data="fake", width=100, height=50,
        )
        worker = FakeWorker(WorkerObservation(description="field", found=True))
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="type_text", arguments={"text": "hello"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Typed text."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Type hello")
        assert result.success is True
        assert result.steps_used == 1

    @patch("chat_agent.gui.manager.press_key", return_value="Pressed: enter")
    @patch("chat_agent.gui.manager.take_screenshot")
    def test_key_press_tool(self, mock_ss, mock_key):
        mock_ss.return_value = ContentPart(
            type="image", media_type="image/png", data="fake", width=100, height=50,
        )
        worker = FakeWorker(WorkerObservation(description="field", found=True))
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="key_press", arguments={"key": "enter"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Pressed enter."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Press enter")
        assert result.success is True


class TestGUIManagerLimits:
    def test_max_steps_exceeded(self):
        # Create a client that always asks to ask_worker
        def make_response(idx):
            return LLMResponse(tool_calls=[
                ToolCall(id=str(idx), name="ask_worker", arguments={"instruction": "look"}),
            ])

        responses = [make_response(i) for i in range(25)]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt", max_steps=3)
        result = manager.execute_task("Keep looking")
        assert result.success is False
        assert "max steps" in result.summary.lower() or "Exceeded" in result.summary

    def test_no_tool_calls_returns_failure(self):
        # LLM responds with text only (no tool calls)
        responses = [
            LLMResponse(content="I cannot do this task."),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Do something")
        assert result.success is False
        assert result.steps_used == 0


class TestGUIManagerScreenshot:
    @patch("chat_agent.gui.manager.take_screenshot")
    def test_screenshot_tool_returns_multimodal(self, mock_ss):
        mock_ss.return_value = ContentPart(
            type="image", media_type="image/png", data="fake", width=100, height=50,
        )
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="screenshot", arguments={}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Saw the screen."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Look at screen")
        assert result.success is True
        assert mock_ss.called


class TestManagerToolDefinitions:
    def test_all_tools_have_names(self):
        names = {t.name for t in MANAGER_TOOLS}
        assert names == {"ask_worker", "click", "type_text", "key_press", "screenshot", "done", "fail"}
