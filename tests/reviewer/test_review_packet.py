"""Tests for deterministic post-review packet construction."""

from chat_agent.llm.schema import Message, ToolCall
from chat_agent.reviewer.review_packet import (
    ReviewPacketConfig,
    _summarize_turn,
    build_post_review_packet,
    render_review_packet,
)


def test_build_post_review_packet_includes_required_sections():
    messages = [
        Message(role="user", content="old turn"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="current user"),
        Message(role="assistant", content="candidate reply"),
    ]
    turn_anchor = 2

    packet = build_post_review_packet(
        messages,
        turn_anchor=turn_anchor,
        config=ReviewPacketConfig(),
    )

    assert packet.latest_user_turn == "current user"
    assert packet.candidate_assistant_reply == "candidate reply"
    assert packet.current_turn_tool_calls_summary == []
    assert packet.current_turn_memory_edit_summary == []


def test_build_post_review_packet_extracts_memory_edit_and_tool_errors():
    tool_call = ToolCall(
        id="tc1",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T15:31:00+08:00",
            "turn_id": "turn-1",
            "requests": [
                {
                    "request_id": "r1",
                    "kind": "append_entry",
                    "target_path": "memory/short-term.md",
                    "payload_text": "entry",
                },
                {
                    "request_id": "r2",
                    "kind": "ensure_index_link",
                    "target_path": "memory/agent/experiences/index.md",
                    "index_path": "memory/agent/experiences/index.md",
                    "link_path": "memory/agent/experiences/2026-02-09-rebirth-naming.md",
                    "link_title": "rebirth",
                },
            ],
        },
    )

    messages = [
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="current"),
        Message(role="assistant", content=None, tool_calls=[tool_call]),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="tc1",
            content='{"status":"failed","errors":[{"code":"apply_failed"}]}',
        ),
        Message(role="assistant", content="final"),
    ]
    turn_anchor = 2

    packet = build_post_review_packet(
        messages,
        turn_anchor=turn_anchor,
        config=ReviewPacketConfig(),
    )

    assert any("memory_edit(" in item for item in packet.current_turn_tool_calls_summary)
    assert len(packet.current_turn_memory_edit_summary) == 2
    assert packet.current_turn_memory_edit_summary[0].target_path == "memory/short-term.md"
    assert packet.current_turn_memory_edit_summary[1].index_path == "memory/agent/experiences/index.md"
    assert any("memory_edit:" in item for item in packet.current_turn_tool_errors)


def test_build_post_review_packet_preserves_current_turn_sections():
    messages = [
        Message(role="user", content="old context"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="current user turn needs review"),
        Message(role="assistant", content="this is a long candidate reply " * 40),
    ]
    turn_anchor = 2

    packet = build_post_review_packet(
        messages,
        turn_anchor=turn_anchor,
        config=ReviewPacketConfig(
            history_turns=2,
            history_turn_max_chars=280,
            tool_preview_max_chars=90,
        ),
    )

    assert packet.latest_user_turn != ""
    assert packet.candidate_assistant_reply != ""
    rendered = render_review_packet(packet)
    assert rendered.startswith("{")


def test_candidate_reply_uses_separate_limit():
    """candidate_assistant_reply uses reply_max_chars, not history_turn_max_chars."""
    long_reply = "x" * 2500
    messages = [
        Message(role="user", content="current"),
        Message(role="assistant", content=long_reply),
    ]

    packet = build_post_review_packet(
        messages,
        turn_anchor=0,
        config=ReviewPacketConfig(
            history_turn_max_chars=1200,
            reply_max_chars=3000,
        ),
    )

    # Reply is 2500 chars, under reply_max_chars=3000, should NOT be truncated.
    assert packet.candidate_assistant_reply == long_reply
    assert not any(
        rec.section == "candidate_assistant_reply" for rec in packet.truncation_report
    )


def test_candidate_reply_truncated_when_exceeds_reply_max_chars():
    """candidate_assistant_reply is truncated when exceeding reply_max_chars."""
    long_reply = "x" * 4000
    messages = [
        Message(role="user", content="current"),
        Message(role="assistant", content=long_reply),
    ]

    packet = build_post_review_packet(
        messages,
        turn_anchor=0,
        config=ReviewPacketConfig(reply_max_chars=2000),
    )

    assert len(packet.candidate_assistant_reply) == 2000
    assert packet.candidate_assistant_reply.endswith("...")
    assert any(
        rec.section == "candidate_assistant_reply" and rec.detail == "trim_reply_budget"
        for rec in packet.truncation_report
    )


def test_summarize_turn_format():
    """_summarize_turn produces U:/A:/Tools: format."""
    tool_call = ToolCall(id="t1", name="get_current_time", arguments={})
    messages = [
        Message(role="user", content="hello"),
        Message(role="assistant", content=None, tool_calls=[tool_call]),
        Message(role="tool", name="get_current_time", tool_call_id="t1", content="ok"),
        Message(role="assistant", content="hi there"),
    ]

    summary, records = _summarize_turn(
        messages, turn_max_chars=1200, tool_result_max_chars=180
    )

    assert summary.startswith("U: hello")
    assert "A: hi there" in summary
    assert "Tools: get_current_time" in summary
    assert records == []
