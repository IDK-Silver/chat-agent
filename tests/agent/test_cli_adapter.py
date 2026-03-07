"""Tests for CLIAdapter slash command handling."""

from unittest.mock import MagicMock

from chat_agent.agent.adapters.cli import CLIAdapter
from chat_agent.cli.commands import CommandResult


def test_reload_resources_command_enqueues_reload_request(tmp_path):
    adapter = CLIAdapter.__new__(CLIAdapter)
    adapter._agent = MagicMock()
    adapter._commands = MagicMock()
    adapter._commands.execute.return_value = CommandResult.RELOAD_RESOURCES
    adapter._commands._console = MagicMock()
    adapter._builder = MagicMock()
    adapter._workspace = MagicMock()
    adapter._agent_os_dir = tmp_path
    adapter._session_mgr = MagicMock()
    adapter._conversation = MagicMock()
    adapter._user_id = "u"
    adapter._display_name = "User"

    should_stop = adapter._handle_command("/reload")

    assert should_stop is False
    adapter._agent.request_reload.assert_called_once_with()
    adapter._builder.update_system_prompt.assert_not_called()
    adapter._builder.reload_boot_files.assert_not_called()


def test_reload_system_prompt_command_enqueues_prompt_only_request(tmp_path):
    adapter = CLIAdapter.__new__(CLIAdapter)
    adapter._agent = MagicMock()
    adapter._commands = MagicMock()
    adapter._commands.execute.return_value = CommandResult.RELOAD_SYSTEM_PROMPT
    adapter._commands._console = MagicMock()
    adapter._builder = MagicMock()
    adapter._workspace = MagicMock()
    adapter._agent_os_dir = tmp_path
    adapter._session_mgr = MagicMock()
    adapter._conversation = MagicMock()
    adapter._user_id = "u"
    adapter._display_name = "User"

    should_stop = adapter._handle_command("/reload system-prompt")

    assert should_stop is False
    adapter._agent.request_reload_system_prompt.assert_called_once_with()
    adapter._builder.update_system_prompt.assert_not_called()
    adapter._builder.reload_boot_files.assert_not_called()
