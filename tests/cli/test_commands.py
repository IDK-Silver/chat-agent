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
