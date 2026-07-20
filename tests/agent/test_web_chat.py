from types import SimpleNamespace

import pytest

from lincy.agent.adapters.web import WebAdapter, WebChatUnavailable
from lincy.agent.schema import OutboundMessage
from lincy.agent.web_chat import WebChatStore


class FakeAgent:
    def __init__(self) -> None:
        self.queued = []
        self.turn_context = SimpleNamespace(metadata={})

    def enqueue(self, msg):
        self.queued.append(msg)


class FailingAgent(FakeAgent):
    def enqueue(self, msg):
        raise RuntimeError("queue down")


def test_web_chat_store_appends_and_limits_recent_events(tmp_path):
    store = WebChatStore(tmp_path / "events.jsonl")

    first = store.append_event(
        kind="message",
        role="user",
        content="hello",
        request_id="r1",
    )
    second = store.append_event(
        kind="status",
        role="system",
        status="queued",
        request_id="r1",
    )

    assert store.recent_events(1) == [second]
    assert store.recent_events(20) == [first, second]


def test_web_adapter_queues_message_and_records_turn_events(tmp_path):
    adapter = WebAdapter(events_path=tmp_path / "events.jsonl")
    agent = FakeAgent()
    adapter.start(agent)

    user_event = adapter.submit_message("  hello web  ")
    request_id = user_event.request_id

    assert len(agent.queued) == 1
    inbound = agent.queued[0]
    assert inbound.channel == "web"
    assert inbound.sender == "web"
    assert inbound.content == "hello web"
    assert inbound.metadata == {
        "source": "web_chat",
        "web_request_id": request_id,
    }

    agent.turn_context.metadata = {"web_request_id": request_id}
    adapter.on_turn_start("web")
    adapter.send(
        OutboundMessage(
            channel="web",
            content="hi back",
            metadata={"web_request_id": request_id},
        )
    )
    adapter.on_turn_complete()

    events = adapter.store.recent_events(10)
    assert [(event.kind, event.role, event.content, event.status) for event in events] == [
        ("message", "user", "hello web", None),
        ("status", "system", None, "queued"),
        ("status", "system", None, "processing"),
        ("message", "assistant", "hi back", None),
        ("status", "system", None, "idle"),
    ]


def test_web_adapter_rejects_blank_message(tmp_path):
    adapter = WebAdapter(events_path=tmp_path / "events.jsonl")
    adapter.start(FakeAgent())

    with pytest.raises(ValueError, match="content is required"):
        adapter.submit_message("   ")


def test_web_adapter_requires_started_agent(tmp_path):
    adapter = WebAdapter(events_path=tmp_path / "events.jsonl")

    with pytest.raises(WebChatUnavailable):
        adapter.submit_message("hello")


def test_web_adapter_records_error_when_enqueue_fails(tmp_path):
    adapter = WebAdapter(events_path=tmp_path / "events.jsonl")
    adapter.start(FailingAgent())

    with pytest.raises(RuntimeError, match="queue down"):
        adapter.submit_message("hello")

    events = adapter.store.recent_events(10)
    assert events[-1].kind == "error"
    assert events[-1].status == "error"
    assert events[-1].content == "queue down"
