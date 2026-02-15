"""Tests for gui/session.py: GUISessionStore persistence."""

from pathlib import Path

from chat_agent.gui.session import GUISessionStore, GUIStepRecord


class TestGUIStepRecord:
    def test_basic_fields(self):
        step = GUIStepRecord(tool="click", args={"bbox": [10, 20, 30, 40]}, result="Clicked")
        assert step.tool == "click"
        assert step.args == {"bbox": [10, 20, 30, 40]}
        assert step.result == "Clicked"


class TestGUISessionStore:
    def test_create_session(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Open Finder")
        assert data.intent == "Open Finder"
        assert data.status == "active"
        assert data.session_id
        assert data.steps == []

        # File exists on disk
        path = tmp_path / "gui" / f"{data.session_id}.json"
        assert path.exists()

    def test_load_session(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        created = store.create("Open Safari")
        loaded = store.load(created.session_id)
        assert loaded.intent == "Open Safari"
        assert loaded.session_id == created.session_id

    def test_load_missing_raises(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        try:
            store.load("nonexistent_id")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_append_step(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Type hello")
        step = GUIStepRecord(tool="type_text", args={"text": "hello"}, result="Typed: 'hello'")
        store.append_step(data.session_id, step)

        reloaded = store.load(data.session_id)
        assert len(reloaded.steps) == 1
        assert reloaded.steps[0].tool == "type_text"
        assert reloaded.steps_used == 1

    def test_append_multiple_steps(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Click and type")
        store.append_step(data.session_id, GUIStepRecord(
            tool="ask_worker", args={"instruction": "Find button"}, result="Found button",
        ))
        store.append_step(data.session_id, GUIStepRecord(
            tool="click", args={"bbox": [10, 20, 30, 40]}, result="Clicked",
        ))

        reloaded = store.load(data.session_id)
        assert len(reloaded.steps) == 2
        assert reloaded.steps_used == 2

    def test_finalize_success(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Open app")
        store.finalize(data.session_id, success=True, summary="Opened app", report="App is running")

        reloaded = store.load(data.session_id)
        assert reloaded.status == "completed"
        assert reloaded.summary == "Opened app"
        assert reloaded.report == "App is running"

    def test_finalize_failure(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Open app")
        store.finalize(data.session_id, success=False, summary="Not found")

        reloaded = store.load(data.session_id)
        assert reloaded.status == "failed"
        assert reloaded.summary == "Not found"

    def test_format_steps_as_context_empty(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Do something")
        assert store.format_steps_as_context(data) == ""

    def test_format_steps_as_context_with_steps(self, tmp_path: Path):
        store = GUISessionStore(tmp_path / "gui")
        data = store.create("Open Finder")
        store.append_step(data.session_id, GUIStepRecord(
            tool="ask_worker", args={"instruction": "Look at screen"}, result="Desktop visible",
        ))
        store.append_step(data.session_id, GUIStepRecord(
            tool="key_press", args={"key": "command+space"}, result="Pressed: command+space",
        ))

        reloaded = store.load(data.session_id)
        context = store.format_steps_as_context(reloaded)
        assert "2 steps completed" in context
        assert "[ask_worker]" in context
        assert "[key_press]" in context
        assert "Desktop visible" in context

    def test_dir_created_on_init(self, tmp_path: Path):
        gui_dir = tmp_path / "session" / "gui"
        assert not gui_dir.exists()
        GUISessionStore(gui_dir)
        assert gui_dir.exists()
