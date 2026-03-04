from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from textual.widgets import RichLog

from chat_agent.tui.app import ChatTextualApp
from chat_agent.tui.controller import TextualController, TurnCancelController
from chat_agent.tui.events import CtxStatusEvent, WarningEvent
from chat_agent.tui.state import UiLogEntry
from chat_agent.tui.sink import QueueUiSink


@pytest.mark.asyncio
async def test_textual_app_renders_status_and_log_from_events():
    sink = QueueUiSink()
    controller = TextualController(
        ui_sink=sink,
        cancel=TurnCancelController(ui_sink=sink),
    )
    app = ChatTextualApp(controller=controller, event_sink=sink)

    sink.set_on_emit(app.wake_ui_event_drain)

    async with app.run_test() as pilot:
        sink.emit(CtxStatusEvent(text="tok 1/10 (10.0%)"))
        sink.emit(WarningEvent(message="warn"))
        await pilot.pause()

        assert "tok 1/10 (10.0%)" in app.status_text
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


def test_textual_app_formats_left_timestamp_with_configured_timezone():
    captured = []
    app = ChatTextualApp(timezone="UTC+8")
    app._ui = SimpleNamespace(
        log=SimpleNamespace(
            write=lambda text, **_: captured.append(text),
            max_scroll_y=0,
            scroll_y=0,
        )
    )

    app._write_log_entry(
        UiLogEntry(
            kind="warning",
            text="warn",
            timestamp=datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc),
        )
    )

    assert captured
    assert captured[0].plain.startswith("22:37:00 ")


@pytest.mark.asyncio
async def test_textual_app_does_not_autofollow_when_user_scrolls_up():
    sink = QueueUiSink()
    controller = TextualController(
        ui_sink=sink,
        cancel=TurnCancelController(ui_sink=sink),
    )
    app = ChatTextualApp(controller=controller, event_sink=sink)
    sink.set_on_emit(app.wake_ui_event_drain)

    async with app.run_test(size=(100, 24)) as pilot:
        for index in range(80):
            sink.emit(WarningEvent(message=f"warn {index}"))
        await pilot.pause()

        log = app.query_one("#log", RichLog)
        log.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        scroll_y = log.scroll_y
        assert log.max_scroll_y > scroll_y

        await pilot.resize_terminal(100, 20)
        await pilot.pause()
        scroll_y = log.scroll_y

        sink.emit(WarningEvent(message="tail update"))
        await pilot.pause()

        assert log.scroll_y == scroll_y
        assert not log.is_vertical_scroll_end


@pytest.mark.asyncio
async def test_textual_app_keeps_autofollow_after_resize_when_at_tail():
    sink = QueueUiSink()
    controller = TextualController(
        ui_sink=sink,
        cancel=TurnCancelController(ui_sink=sink),
    )
    app = ChatTextualApp(controller=controller, event_sink=sink)
    sink.set_on_emit(app.wake_ui_event_drain)

    async with app.run_test(size=(100, 24)) as pilot:
        for index in range(80):
            sink.emit(WarningEvent(message=f"warn {index}"))
        await pilot.pause()

        log = app.query_one("#log", RichLog)
        log.scroll_end(animate=False, immediate=True, x_axis=False)
        await pilot.pause()
        assert log.is_vertical_scroll_end

        await pilot.resize_terminal(100, 16)
        await pilot.pause()

        sink.emit(WarningEvent(message="tail update after resize"))
        await pilot.pause()

        assert log.is_vertical_scroll_end
