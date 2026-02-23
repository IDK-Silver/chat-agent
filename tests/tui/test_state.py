from chat_agent.tui.events import (
    AssistantTextEvent,
    CtxStatusEvent,
    InterruptStateEvent,
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

