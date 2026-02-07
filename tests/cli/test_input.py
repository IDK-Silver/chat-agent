"""Tests for CLI input component."""

from unittest.mock import MagicMock

from chat_agent.cli.input import ChatInput


def test_get_input_uses_dynamic_prompt_and_refresh_interval():
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    chat_input._session.prompt.return_value = "hello"

    result = chat_input.get_input()

    assert result == "hello"
    chat_input._session.prompt.assert_called_once()
    args, kwargs = chat_input._session.prompt.call_args
    assert callable(args[0])
    assert args[0].__self__ is chat_input
    assert args[0].__func__ is ChatInput._get_prompt
    assert kwargs["refresh_interval"] == 1.0


def test_get_input_returns_none_on_interrupt():
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    chat_input._session.prompt.side_effect = KeyboardInterrupt()

    result = chat_input.get_input()

    assert result is None
