"""Tests for ChatInput parameter acceptance."""

from unittest.mock import patch

from chat_agent.cli.input import ChatInput


class TestChatInputBottomToolbar:
    @patch("chat_agent.cli.input.FileHistory")
    def test_accepts_bottom_toolbar_callable(self, mock_history):
        """ChatInput should accept a bottom_toolbar callable without error."""
        toolbar = lambda: "ctx: 0 / 400,000 (0.0%)"
        chat_input = ChatInput(bottom_toolbar=toolbar)
        assert chat_input is not None

    @patch("chat_agent.cli.input.FileHistory")
    def test_accepts_none_toolbar(self, mock_history):
        """ChatInput with no toolbar should work (default)."""
        chat_input = ChatInput()
        assert chat_input is not None
