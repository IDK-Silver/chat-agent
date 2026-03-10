"""Tests for CLI slash command parsing."""

from unittest.mock import MagicMock

from chat_agent.cli.commands import CommandHandler, CommandResult


def test_reload_without_args_reloads_all_resources():
    handler = CommandHandler(MagicMock())

    result = handler.execute("/reload")

    assert result == CommandResult.RELOAD_RESOURCES


def test_reload_all_reloads_all_resources():
    handler = CommandHandler(MagicMock())

    result = handler.execute("/reload all")

    assert result == CommandResult.RELOAD_RESOURCES


def test_reload_system_prompt_keeps_specific_target():
    handler = CommandHandler(MagicMock())

    result = handler.execute("/reload system-prompt")

    assert result == CommandResult.RELOAD_SYSTEM_PROMPT


def test_reload_unknown_target_prints_usage():
    console = MagicMock()
    handler = CommandHandler(console)

    result = handler.execute("/reload nope")

    assert result == CommandResult.CONTINUE
    console.print_error.assert_called_once_with("Unknown reload target: nope")
    console.print_info.assert_called_once_with("Usage: /reload [all|system-prompt]")


def test_shell_input_forwards_text_to_manager():
    console = MagicMock()
    manager = MagicMock()
    manager.send_input.return_value = "Forwarded input to shell session sh_0001."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-input hello world")

    assert result == CommandResult.CONTINUE
    manager.send_input.assert_called_once_with("hello world", session_id=None)
    console.print_info.assert_called_once_with(
        "Forwarded input to shell session sh_0001."
    )


def test_shell_input_accepts_explicit_session_id():
    console = MagicMock()
    manager = MagicMock()
    manager.send_input.return_value = "Forwarded input to shell session sh_0002."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-input sh_0002 123456")

    assert result == CommandResult.CONTINUE
    manager.send_input.assert_called_once_with("123456", session_id="sh_0002")


def test_shell_up_forwards_to_manager():
    console = MagicMock()
    manager = MagicMock()
    manager.send_up.return_value = "Sent Up to shell session sh_0001."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-up")

    assert result == CommandResult.CONTINUE
    manager.send_up.assert_called_once_with(session_id=None)
    console.print_info.assert_called_once_with("Sent Up to shell session sh_0001.")


def test_shell_down_accepts_explicit_session_id():
    console = MagicMock()
    manager = MagicMock()
    manager.send_down.return_value = "Sent Down to shell session sh_0002."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-down sh_0002")

    assert result == CommandResult.CONTINUE
    manager.send_down.assert_called_once_with(session_id="sh_0002")


def test_shell_left_forwards_to_manager():
    console = MagicMock()
    manager = MagicMock()
    manager.send_left.return_value = "Sent Left to shell session sh_0001."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-left")

    assert result == CommandResult.CONTINUE
    manager.send_left.assert_called_once_with(session_id=None)
    console.print_info.assert_called_once_with("Sent Left to shell session sh_0001.")


def test_shell_right_accepts_explicit_session_id():
    console = MagicMock()
    manager = MagicMock()
    manager.send_right.return_value = "Sent Right to shell session sh_0002."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-right sh_0002")

    assert result == CommandResult.CONTINUE
    manager.send_right.assert_called_once_with(session_id="sh_0002")


def test_shell_tab_forwards_to_manager():
    console = MagicMock()
    manager = MagicMock()
    manager.send_tab.return_value = "Sent Tab to shell session sh_0001."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-tab")

    assert result == CommandResult.CONTINUE
    manager.send_tab.assert_called_once_with(session_id=None)
    console.print_info.assert_called_once_with("Sent Tab to shell session sh_0001.")


def test_shell_esc_forwards_to_manager():
    console = MagicMock()
    manager = MagicMock()
    manager.send_escape.return_value = "Sent Escape to shell session sh_0001."
    handler = CommandHandler(console)
    handler.set_shell_task_manager(manager)

    result = handler.execute("/shell-esc")

    assert result == CommandResult.CONTINUE
    manager.send_escape.assert_called_once_with(session_id=None)
    console.print_info.assert_called_once_with("Sent Escape to shell session sh_0001.")


def test_shell_commands_can_run_while_processing():
    handler = CommandHandler(MagicMock())

    assert handler.can_execute_while_processing("/shell-status") is True
    assert handler.can_execute_while_processing("/shell-input hello") is True
    assert handler.can_execute_while_processing("/shell-up") is True
    assert handler.can_execute_while_processing("/shell-down") is True
    assert handler.can_execute_while_processing("/shell-left") is True
    assert handler.can_execute_while_processing("/shell-right") is True
    assert handler.can_execute_while_processing("/shell-tab") is True
    assert handler.can_execute_while_processing("/shell-esc") is True
    assert handler.can_execute_while_processing("/clear") is False
