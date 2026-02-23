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


def test_get_input_passes_rprompt_callback():
    toolbar = lambda: "ctx: 1 / 10"
    chat_input = ChatInput(bottom_toolbar=toolbar)
    chat_input._session = MagicMock()
    chat_input._session.prompt.return_value = "hello"

    chat_input.get_input()

    _, kwargs = chat_input._session.prompt.call_args
    assert kwargs["rprompt"] is toolbar


def test_get_input_returns_none_on_eof():
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    chat_input._session.prompt.side_effect = EOFError()

    result = chat_input.get_input()

    assert result is None


def test_double_ctrl_c_sets_exit_and_returns_none():
    """Double Ctrl+C sets _exit_requested, get_input() returns None."""
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    # Simulate: double Ctrl+C sets _exit_requested, prompt returns empty
    chat_input._exit_requested = True
    chat_input._session.prompt.return_value = ""

    result = chat_input.get_input()

    assert result is None
    # Flag is reset after consumption
    assert chat_input._exit_requested is False


def test_single_ctrl_c_does_not_set_exit():
    """Single Ctrl+C should not set _exit_requested."""
    chat_input = ChatInput()

    # After construction, _exit_requested should be False
    assert chat_input._exit_requested is False
    # _last_ctrl_c_time starts at 0
    assert chat_input._last_ctrl_c_time == 0.0


def test_double_esc_sets_history_select():
    """Double ESC sets _history_select_requested."""
    chat_input = ChatInput()

    # Simulate double ESC setting the flag
    chat_input._history_select_requested = True

    assert chat_input.wants_history_select is True
    # Flag auto-resets after read
    assert chat_input._history_select_requested is False


def test_wants_history_select_resets_after_read():
    """wants_history_select property resets flag after reading."""
    chat_input = ChatInput()
    chat_input._history_select_requested = True

    # First read returns True
    assert chat_input.wants_history_select is True
    # Second read returns False (already reset)
    assert chat_input.wants_history_select is False


def test_set_prefill():
    """set_prefill passes default text to next prompt."""
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    chat_input._session.prompt.return_value = "prefilled"
    chat_input.set_prefill("hello world")

    chat_input.get_input()

    _, kwargs = chat_input._session.prompt.call_args
    assert kwargs["default"] == "hello world"


def test_prefill_cleared_after_use():
    """Prefill is consumed after one prompt."""
    chat_input = ChatInput()
    chat_input._session = MagicMock()
    chat_input._session.prompt.return_value = ""
    chat_input.set_prefill("once")

    chat_input.get_input()
    chat_input.get_input()

    # Second call should have empty default
    _, kwargs = chat_input._session.prompt.call_args
    assert kwargs["default"] == ""
