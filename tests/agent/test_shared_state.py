from datetime import datetime, timezone

from chat_agent.agent.shared_state import SharedStateStore


def test_record_shared_outbound_increments_rev_and_persists(tmp_path):
    path = tmp_path / "shared_state.json"
    store = SharedStateStore(path)

    rev1 = store.record_shared_outbound(
        scope_id="discord:dm:idksilver",
        channel="discord",
        recipient="老公",
        body="first",
        ts=datetime(2026, 2, 24, 13, 55, tzinfo=timezone.utc),
    )
    rev2 = store.record_shared_outbound(
        scope_id="discord:dm:idksilver",
        channel="discord",
        recipient="老公",
        body="second",
        ts=datetime(2026, 2, 24, 13, 56, tzinfo=timezone.utc),
    )
    assert (rev1, rev2) == (1, 2)
    assert store.get_current_rev("discord:dm:idksilver") == 2

    store.save()
    loaded = SharedStateStore.load_or_init(path)
    assert loaded.loaded is True
    assert loaded.store.get_current_rev("discord:dm:idksilver") == 2


def test_build_common_ground_text_filters_by_anchor_and_limits_entries(tmp_path):
    store = SharedStateStore(tmp_path / "shared_state.json")
    scope = "discord:dm:idksilver"
    for i in range(1, 6):
        store.record_shared_outbound(
            scope_id=scope,
            channel="discord",
            recipient="老公",
            body=f"message {i}",
        )

    text = store.build_common_ground_text(
        scope_id=scope,
        upto_rev=3,
        current_rev=5,
        max_entries=8,
        max_chars=2000,
        max_entry_chars=100,
    )
    assert text is not None
    assert "message_time_shared_rev: 3" in text
    assert "turn_start_shared_rev: 5" in text
    assert "message 1" in text
    assert "message 3" in text
    assert "message 4" not in text
    assert "message 5" not in text


def test_build_common_ground_synthetic_messages_returns_assistant_tool_pair(tmp_path):
    store = SharedStateStore(tmp_path / "shared_state.json")
    store.record_shared_outbound(
        scope_id="discord:dm:idksilver",
        channel="discord",
        recipient="老公",
        body="三視圖報價",
    )
    pair = store.build_common_ground_synthetic_messages(
        scope_id="discord:dm:idksilver",
        upto_rev=1,
        current_rev=2,
        max_entries=8,
        max_chars=2000,
        max_entry_chars=100,
    )
    assert pair is not None
    call_msg, tool_msg = pair
    assert call_msg.role == "assistant"
    assert call_msg.tool_calls
    assert call_msg.tool_calls[0].name == "_load_common_ground_at_message_time"
    assert tool_msg.role == "tool"
    assert tool_msg.name == "_load_common_ground_at_message_time"
    assert isinstance(tool_msg.content, str)
    assert "三視圖報價" in tool_msg.content

