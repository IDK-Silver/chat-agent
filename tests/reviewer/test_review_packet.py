"""Tests for deterministic post-review packet construction."""

from chat_agent.llm.schema import Message, ToolCall
from chat_agent.reviewer.review_packet import (
    ReviewPacketConfig,
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
    assert packet.chars_after <= packet.chars_before


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


def test_build_post_review_packet_drops_oldest_context_under_budget():
    long_text = "x" * 1200
    messages = [
        Message(role="user", content="u1"),
        Message(role="assistant", content=long_text),
        Message(role="user", content="u2"),
        Message(role="assistant", content=long_text),
        Message(role="user", content="u3"),
        Message(role="assistant", content=long_text),
        Message(role="user", content="u4"),
        Message(role="assistant", content=long_text),
        Message(role="user", content="current"),
        Message(role="assistant", content="final"),
    ]
    turn_anchor = 8

    packet = build_post_review_packet(
        messages,
        turn_anchor=turn_anchor,
        config=ReviewPacketConfig(
            review_window_turns=6,
            review_max_chars=1800,
            review_turn_max_chars=600,
            review_tool_result_max_chars=120,
        ),
    )

    assert packet.chars_after <= 1800
    assert any(
        rec.section == "recent_context_tail" and rec.action == "drop"
        for rec in packet.truncation_report
    )


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
            review_window_turns=2,
            review_max_chars=1000,
            review_turn_max_chars=280,
            review_tool_result_max_chars=90,
        ),
    )

    assert packet.chars_after <= 1000
    assert packet.latest_user_turn != ""
    assert packet.candidate_assistant_reply != ""
    # Ensure renderer can always produce JSON even after aggressive truncation.
    rendered = render_review_packet(packet)
    assert rendered.startswith("{")
