from chat_agent.tui.controller import TextualController, TurnCancelController
from chat_agent.tui.events import InterruptStateEvent
from chat_agent.tui.sink import QueueUiSink


def test_turn_cancel_controller_emits_state_transitions():
    sink = QueueUiSink()
    cancel = TurnCancelController(ui_sink=sink)

    cancel.begin_turn()
    cancel.request()
    cancel.mark_pending()
    cancel.acknowledge()
    cancel.complete()

    phases = [
        event.phase
        for event in sink.drain()
        if isinstance(event, InterruptStateEvent)
    ]
    assert phases == ["idle", "requested", "pending", "acknowledged", "completed"]
    assert cancel.is_requested() is False


def test_textual_controller_submit_and_ctx_refresh():
    sink = QueueUiSink()
    seen: list[str] = []
    controller = TextualController(
        ui_sink=sink,
        on_submit=seen.append,
        ctx_provider=lambda: "tok 10/100 (10.0%)",
    )

    controller.submit_input("hello")
    controller.refresh_ctx_status()

    assert seen == ["hello"]
    events = sink.drain()
    assert any(getattr(e, "text", "") == "tok 10/100 (10.0%)" for e in events)
