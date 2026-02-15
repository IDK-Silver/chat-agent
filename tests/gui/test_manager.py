"""Tests for gui/manager.py: GUIManager agentic loop."""

from pathlib import Path
from unittest.mock import patch

from chat_agent.gui.manager import GUIManager, MANAGER_TOOLS
from chat_agent.gui.session import GUISessionStore, GUIStepRecord
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
        assert names == {"ask_worker", "click", "type_text", "key_press", "screenshot", "capture_screenshot", "paste_screenshot", "activate_app", "wait", "get_active_app", "done", "fail", "report_problem"}


class TestGUIManagerReport:
    def test_done_with_report(self):
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={
                    "summary": "Task completed.",
                    "report": "Found 3 items on screen.",
                }),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Check screen")
        assert result.success is True
        assert result.report == "Found 3 items on screen."

    def test_fail_with_report(self):
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="fail", arguments={
                    "reason": "App not found.",
                    "report": "Searched in Dock and Spotlight.",
                }),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Open app")
        assert result.success is False
        assert result.report == "Searched in Dock and Spotlight."

    def test_done_without_report_defaults_empty(self):
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Do task")
        assert result.report == ""


class TestGUIManagerSessionStore:
    def test_session_id_returned(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt", session_store=store)
        result = manager.execute_task("Open Finder")
        assert result.session_id != ""
        # Session file exists
        loaded = store.load(result.session_id)
        assert loaded.status == "completed"
        assert loaded.summary == "Done."

    def test_steps_recorded(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Look"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="Desktop visible", found=True))
        manager = GUIManager(client, worker, "system prompt", session_store=store)
        result = manager.execute_task("Check")
        loaded = store.load(result.session_id)
        assert len(loaded.steps) == 1
        assert loaded.steps[0].tool == "ask_worker"

    def test_no_session_store_backward_compat(self):
        """Without session_store, behavior unchanged and session_id is empty."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Open Finder")
        assert result.session_id == ""
        assert result.success is True

    def test_resume_session(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        # Simulate a previous session with steps
        data = store.create("Open Safari")
        store.append_step(data.session_id, GUIStepRecord(
            tool="ask_worker", args={"instruction": "Look"}, result="Desktop visible",
        ))

        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Resumed and done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt", session_store=store)
        result = manager.execute_task("Continue opening Safari", session_id=data.session_id)
        assert result.success is True
        assert result.session_id == data.session_id


class TestGUIManagerOnStepCallback:
    def test_on_step_callback_called(self):
        """Callback is called for each non-terminal step + the terminal step."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Look"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="Found it", found=True))

        calls: list[tuple] = []

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            calls.append((tc.name, res, step, max_steps, elapsed, total, wt))

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        result = manager.execute_task("Check")
        assert result.success is True
        # ask_worker (step 1) + done (step 1 since done uses steps+1 before increment)
        assert len(calls) == 2
        assert calls[0][0] == "ask_worker"
        assert calls[0][2] == 1  # step number
        assert calls[0][4] >= 0  # elapsed_sec is non-negative
        assert calls[0][5] >= 0  # total_elapsed_sec is non-negative
        assert calls[1][0] == "done"
        assert calls[1][5] >= calls[0][5]  # total increases monotonically

    def test_on_step_callback_receives_terminal(self):
        """done/fail also trigger callback."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="fail", arguments={"reason": "App not found."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))

        calls: list[tuple[str, str]] = []

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            calls.append((tc.name, res))

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        result = manager.execute_task("Open app")
        assert result.success is False
        assert len(calls) == 1
        assert calls[0][0] == "fail"
        assert "App not found" in calls[0][1]

    def test_on_step_callback_exception_ignored(self):
        """Callback raising an exception does not break the loop."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Look"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            raise RuntimeError("UI crash")

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        result = manager.execute_task("Check")
        assert result.success is True

    def test_on_step_none_backward_compat(self):
        """on_step=None does not cause errors."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Look"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Check")
        assert result.success is True

    def test_elapsed_sec_in_result(self):
        """GUITaskResult includes total elapsed time."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Quick task")
        assert result.elapsed_sec >= 0


class TestGUIManagerReportProblem:
    def test_report_problem_is_terminal(self):
        """report_problem stops the loop with needs_input=True."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="report_problem", arguments={
                    "problem": "Cannot find contact named 'foo'.",
                }),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Send message to foo")
        assert result.success is False
        assert result.needs_input is True
        assert "Cannot find contact" in result.summary

    def test_report_problem_with_report(self):
        """report_problem passes report field through."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="report_problem", arguments={
                    "problem": "Target not found.",
                    "report": "Tried scrolling and searching.",
                }),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))
        manager = GUIManager(client, worker, "system prompt")
        result = manager.execute_task("Find target")
        assert result.needs_input is True
        assert result.report == "Tried scrolling and searching."

    def test_done_and_fail_have_needs_input_false(self):
        """done and fail should not set needs_input."""
        for name, args in [
            ("done", {"summary": "OK"}),
            ("fail", {"reason": "Broken"}),
        ]:
            responses = [
                LLMResponse(tool_calls=[
                    ToolCall(id="1", name=name, arguments=args),
                ]),
            ]
            client = FakeManagerClient(responses)
            worker = FakeWorker(WorkerObservation(description="screen", found=True))
            manager = GUIManager(client, worker, "system prompt")
            result = manager.execute_task("Test")
            assert result.needs_input is False, f"{name} should have needs_input=False"

    def test_report_problem_callback_called(self):
        """on_step callback is invoked for report_problem."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="report_problem", arguments={
                    "problem": "Stuck.",
                }),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))

        calls: list[str] = []

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            calls.append(tc.name)

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        manager.execute_task("Test")
        assert "report_problem" in calls


class TestGUIManagerWorkerTiming:
    def test_ask_worker_passes_timing_to_callback(self):
        """Callback receives worker_timing dict for ask_worker steps."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="ask_worker", arguments={"instruction": "Look"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="Found it", found=True))

        timings: list[dict[str, float] | None] = []

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            timings.append(wt)

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        manager.execute_task("Check")

        # ask_worker step should have timing dict
        assert timings[0] is not None
        assert "screenshot" in timings[0]
        assert "inference" in timings[0]
        assert timings[0]["screenshot"] >= 0
        assert timings[0]["inference"] >= 0
        # done step should have no timing
        assert timings[1] is None

    def test_non_ask_worker_has_no_timing(self):
        """Non-ask_worker steps get worker_timing=None."""
        responses = [
            LLMResponse(tool_calls=[
                ToolCall(id="1", name="key_press", arguments={"key": "enter"}),
            ]),
            LLMResponse(tool_calls=[
                ToolCall(id="2", name="done", arguments={"summary": "Done."}),
            ]),
        ]
        client = FakeManagerClient(responses)
        worker = FakeWorker(WorkerObservation(description="screen", found=True))

        timings: list[dict[str, float] | None] = []

        def on_step(tc, res, step, max_steps, elapsed, total, wt):
            timings.append(wt)

        manager = GUIManager(client, worker, "system prompt", on_step=on_step)
        with patch("chat_agent.gui.manager.press_key", return_value="Pressed: enter"):
            manager.execute_task("Press enter")

        assert timings[0] is None  # key_press
        assert timings[1] is None  # done
