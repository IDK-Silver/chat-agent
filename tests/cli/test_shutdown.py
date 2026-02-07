"""Tests for CLI shutdown module."""

from datetime import datetime, timezone as tz
from unittest.mock import MagicMock

from chat_agent.context import Conversation
from chat_agent.llm.schema import LLMResponse, ToolCall
from chat_agent.cli.shutdown import perform_shutdown, _has_conversation_content
from chat_agent.reviewer.schema import PostReviewResult, RequiredAction


class TestHasConversationContent:
    def test_empty_conversation(self):
        """Returns False for new conversation."""
        c = Conversation()
        assert _has_conversation_content(c) is False

    def test_with_user_message(self):
        """Returns True when user messages exist."""
        c = Conversation()
        c.add("user", "hello")
        assert _has_conversation_content(c) is True

    def test_only_assistant_messages(self):
        """Returns False for assistant-only messages."""
        c = Conversation()
        c.add("assistant", "hi there")
        assert _has_conversation_content(c) is False


class TestPerformShutdown:
    def _make_mocks(self, tmp_path):
        """Create mock objects for shutdown testing."""
        client = MagicMock()
        conversation = Conversation()
        conversation.add("user", "hello")
        conversation.add("assistant", "hi")
        builder = MagicMock()
        builder.build.return_value = []
        registry = MagicMock()
        registry.get_definitions.return_value = []
        console = MagicMock()
        console.spinner.return_value.__enter__ = MagicMock()
        console.spinner.return_value.__exit__ = MagicMock(return_value=False)

        # Create workspace with shutdown prompt
        workspace = MagicMock()
        workspace.get_agent_prompt.return_value = "Save memories now."

        return client, conversation, builder, registry, console, workspace

    def test_sends_shutdown_prompt(self, tmp_path):
        """Shutdown prompt is loaded and sent to LLM."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)
        client.chat_with_tools.return_value = LLMResponse(content="Done.", tool_calls=[])

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is True
        workspace.get_agent_prompt.assert_called_once_with(
            "brain", "shutdown", current_user="test-user"
        )
        client.chat_with_tools.assert_called_once()

    def test_executes_tool_calls(self, tmp_path):
        """Tool calls from LLM are executed."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)

        tool_call = ToolCall(id="tc1", name="write_file", arguments={"path": "test.md"})
        # First response has tool calls, second has none
        client.chat_with_tools.side_effect = [
            LLMResponse(content=None, tool_calls=[tool_call]),
            LLMResponse(content="Saved.", tool_calls=[]),
        ]
        registry.execute.return_value = "ok"

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is True
        registry.execute.assert_called_once_with(tool_call)
        assert client.chat_with_tools.call_count == 2

    def test_max_iterations(self, tmp_path):
        """Stops after max iterations even if tool calls continue."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)

        tool_call = ToolCall(id="tc1", name="write_file", arguments={})
        # Always return tool calls
        client.chat_with_tools.return_value = LLMResponse(
            content=None, tool_calls=[tool_call]
        )
        registry.execute.return_value = "ok"

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is True
        # 1 initial call + 20 loop iterations = 21
        assert client.chat_with_tools.call_count == 21

    def test_keyboard_interrupt_returns_false(self, tmp_path):
        """KeyboardInterrupt during shutdown returns False."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)

        client.chat_with_tools.side_effect = KeyboardInterrupt()

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is False

    def test_missing_prompt_skips(self, tmp_path):
        """Returns True when shutdown prompt not found."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)
        workspace.get_agent_prompt.side_effect = FileNotFoundError("not found")

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is True
        client.chat_with_tools.assert_not_called()

    def test_shutdown_prompt_uses_last_user_timestamp(self, tmp_path):
        """Shutdown prompt inherits the latest user timestamp."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)
        client.chat_with_tools.return_value = LLMResponse(content="Done.", tool_calls=[])

        last_user_time = datetime(2026, 2, 7, 10, 20, tzinfo=tz.utc)
        conversation.add("user", "latest user message", timestamp=last_user_time)

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
        )

        assert result is True
        shutdown_messages = [
            m for m in conversation.get_messages()
            if m.role == "user" and m.content == "Save memories now."
        ]
        assert len(shutdown_messages) == 1
        assert shutdown_messages[0].timestamp == last_user_time

    def test_shutdown_reviewer_retry_path(self, tmp_path):
        """Shutdown reviewer can request one retry action."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)
        reviewer = MagicMock()

        # First pass: no tool call, second pass: retry emits one tool call, then done.
        tool_call = ToolCall(
            id="tc1",
            name="edit_file",
            arguments={"path": "memory/short-term.md", "old_string": "a", "new_string": "b"},
        )
        client.chat_with_tools.side_effect = [
            LLMResponse(content="first done", tool_calls=[]),
            LLMResponse(content=None, tool_calls=[tool_call]),
            LLMResponse(content="retry done", tool_calls=[]),
        ]
        reviewer.review.side_effect = [
            PostReviewResult(
                passed=False,
                violations=["missing_short_term_update"],
                required_actions=[
                    RequiredAction(
                        code="update_short_term",
                        description="Update short-term memory",
                        tool="write_or_edit",
                        target_path="memory/short-term.md",
                    )
                ],
                retry_instruction="Do it now.",
            ),
            PostReviewResult(
                passed=True,
                violations=[],
                required_actions=[],
                retry_instruction="",
            ),
        ]
        registry.execute.return_value = "ok"

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
            reviewer=reviewer,
            reviewer_max_retries=1,
            reviewer_warn_on_failure=True,
        )

        assert result is True
        assert reviewer.review.call_count == 2
        assert registry.execute.call_count == 1

    def test_shutdown_reviewer_warning_on_failure(self, tmp_path):
        """Reviewer failure shows warning and keeps saved memories."""
        client, conversation, builder, registry, console, workspace = self._make_mocks(tmp_path)
        reviewer = MagicMock()

        client.chat_with_tools.return_value = LLMResponse(content="done", tool_calls=[])
        reviewer.review.return_value = None

        result = perform_shutdown(
            client, conversation, builder, registry,
            console, workspace, "test-user",
            reviewer=reviewer,
            reviewer_max_retries=1,
            reviewer_warn_on_failure=True,
        )

        assert result is True
        console.print_warning.assert_called_once()
