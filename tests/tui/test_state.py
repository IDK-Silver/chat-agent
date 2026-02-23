from datetime import datetime, timezone

from chat_agent.tui.events import (
    AssistantTextEvent,
    CtxStatusEvent,
    InboundMessageEvent,
    InterruptStateEvent,
    OutboundMessageEvent,
    ProcessingFinishedEvent,
    ProcessingStartedEvent,
    WarningEvent,
)
from chat_agent.tui.state import UiState


def test_ui_state_tracks_ctx_busy_interrupt_and_log():
    state = UiState()

    state.append_event(CtxStatusEvent(text="ctx 123/1000 (12.3%)"))
    state.append_event(ProcessingStartedEvent(channel="gmail", sender="alice"))
    state.append_event(AssistantTextEvent(content="thinking"))
    state.append_event(WarningEvent(message="memory_edit warnings"))
    state.append_event(InterruptStateEvent(phase="requested", message="Interrupt requested"))
    state.append_event(ProcessingFinishedEvent(interrupted=True))

    assert state.ctx_status == "ctx 123/1000 (12.3%)"
    assert state.busy is False
    assert state.interrupt_state == "requested"
    assert state.interrupt_message == "Interrupt requested"
    assert [entry.kind for entry in state.log] == [
        "processing",
        "assistant",
        "warning",
        "info",
    ]


def test_ui_state_suppresses_immediate_duplicate_rows():
    state = UiState()

    state.append_event(AssistantTextEvent(content="same"))
    state.append_event(AssistantTextEvent(content="same"))
    state.append_event(WarningEvent(message="dup"))
    state.append_event(WarningEvent(message="dup"))

    assert [(entry.kind, entry.text) for entry in state.log] == [
        ("assistant", "same"),
        ("warning", "dup"),
    ]


def test_ui_state_ignores_outbound_event_rows():
    state = UiState()
    state.append_event(OutboundMessageEvent(channel="cli", recipient="yufeng", content="hello"))
    assert state.log == []


def test_ui_state_logs_turn_complete_row_for_visual_separation():
    state = UiState()

    state.append_event(ProcessingStartedEvent(channel="cli", sender="yufeng"))
    state.append_event(ProcessingFinishedEvent(interrupted=False))

    assert [(entry.kind, entry.text) for entry in state.log] == [
        ("processing", "source=cli/yufeng"),
        ("info", "Turn complete"),
    ]


def test_ui_state_formats_inbound_timestamp_with_configured_timezone():
    state = UiState(timezone="UTC+8")

    state.append_event(
        InboundMessageEvent(
            timestamp=datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc),
            channel="line",
            sender="friend",
            content="hi",
        )
    )

    assert "03/01 22:37:00 source=line/friend" in state.log[0].text
