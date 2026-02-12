"""Tests for conversation flattening for reviewer LLMs."""

from chat_agent.llm.schema import Message, ToolCall
from chat_agent.reviewer.flatten import flatten_for_review


class TestFlattenForReview:
    def test_plain_conversation(self):
        messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        result = flatten_for_review(messages)
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    def test_strips_system_messages(self):
        messages = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="test"),
        ]
        result = flatten_for_review(messages)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_flattens_tool_calls(self):
        messages = [
            Message(role="user", content="what time is it?"),
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="get_current_time", arguments={"timezone": "Asia/Taipei"}),
                ],
            ),
            Message(role="tool", content="2026-02-07 18:30", tool_call_id="1", name="get_current_time"),
            Message(role="assistant", content="It's 18:30."),
        ]
        result = flatten_for_review(messages)
        assert len(result) == 3
        assert result[0].role == "user"
        # Flattened tool call + result
        assert result[1].role == "assistant"
        assert "get_current_time" in result[1].content
        assert "2026-02-07 18:30" in result[1].content or "get_current_time" in result[1].content
        # Final response
        assert result[2].role == "assistant"
        assert result[2].content == "It's 18:30."

    def test_multiple_tool_calls(self):
        messages = [
            Message(role="user", content="test"),
            Message(
                role="assistant",
                content="Let me check.",
                tool_calls=[
                    ToolCall(id="1", name="read_file", arguments={"path": "memory/a.md"}),
                    ToolCall(id="2", name="read_file", arguments={"path": "memory/b.md"}),
                ],
            ),
            Message(role="tool", content="content a", tool_call_id="1", name="read_file"),
            Message(role="tool", content="content b", tool_call_id="2", name="read_file"),
        ]
        result = flatten_for_review(messages)
        assert len(result) == 2
        flat = result[1].content
        assert "Let me check." in flat
        assert "read_file(path=memory/a.md)" in flat
        assert "read_file(path=memory/b.md)" in flat
        assert "[read_file: content a]" in flat
        assert "[read_file: content b]" in flat

    def test_truncates_long_results(self):
        long_content = "x" * 1000
        messages = [
            Message(role="user", content="test"),
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="read_file", arguments={"path": "big.md"}),
                ],
            ),
            Message(role="tool", content=long_content, tool_call_id="1", name="read_file"),
        ]
        result = flatten_for_review(messages)
        # Long content gets summarized with line count
        assert "lines)" in result[1].content
        assert len(result[1].content) < 500

    def test_boot_sequence_flattened(self):
        """Simulates a typical boot sequence with many tool calls."""
        messages = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="hello"),
            # Boot: multiple tool calls
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="get_current_time", arguments={"timezone": "Asia/Taipei"}),
                    ToolCall(id="2", name="read_file", arguments={"path": "memory/agent/persona.md"}),
                    ToolCall(id="3", name="read_file", arguments={"path": "memory/agent/short-term.md"}),
                ],
            ),
            Message(role="tool", content="2026-02-07 18:30", tool_call_id="1", name="get_current_time"),
            Message(role="tool", content="persona data", tool_call_id="2", name="read_file"),
            Message(role="tool", content="short term data", tool_call_id="3", name="read_file"),
            # Second round of tool calls
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id="4", name="execute_shell", arguments={"command": "cat memory/agent/skills/index.md"}),
                ],
            ),
            Message(role="tool", content="skills index", tool_call_id="4", name="execute_shell"),
            # Final response
            Message(role="assistant", content="Hello! Welcome back."),
        ]
        result = flatten_for_review(messages)
        # user + 2 flattened tool groups + final response
        assert len(result) == 4
        assert all(m.role in ("user", "assistant") for m in result)
        # All tool names visible
        combined = "\n".join(m.content or "" for m in result)
        assert "get_current_time" in combined
        assert "read_file" in combined
        assert "execute_shell" in combined
