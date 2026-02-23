import pytest

from chat_agent.tui.app import ChatTextualApp
from chat_agent.tui.controller import TextualController, TurnCancelController
from chat_agent.tui.events import CtxStatusEvent, WarningEvent
from chat_agent.tui.sink import QueueUiSink


@pytest.mark.asyncio
async def test_textual_app_renders_status_and_log_from_events():
    sink = QueueUiSink()
    controller = TextualController(
        ui_sink=sink,
        cancel=TurnCancelController(ui_sink=sink),
    )
    app = ChatTextualApp(controller=controller)

    sink.set_on_emit(app.post_ui_event)

    async with app.run_test() as pilot:
        app.post_ui_event(CtxStatusEvent(text="ctx 1/10 (10.0%)"))
        app.post_ui_event(WarningEvent(message="warn"))
        await pilot.pause()

        assert "ctx 1/10 (10.0%)" in app.status_text
        assert any("warn" in line for line in app.log_lines)


@pytest.mark.asyncio
async def test_textual_app_ctrl_c_clears_input():
    app = ChatTextualApp()

    async with app.run_test() as pilot:
        input_widget = app.query_one("#input")
        input_widget.insert("hello")
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert input_widget.text == ""


@pytest.mark.asyncio
async def test_textual_app_ctrl_r_history_modal_prefills_selection():
    sink = QueueUiSink()
    selected: list[int] = []
    controller = TextualController(
        ui_sink=sink,
        on_history_options=lambda: ["latest message", "older message"],
        on_history_select=lambda idx: (selected.append(idx) or ["latest message", "older message"][idx]),
    )
    app = ChatTextualApp(controller=controller)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()

        input_widget = app.query_one("#input")
        assert input_widget.text == "older message"
        assert selected == [1]
