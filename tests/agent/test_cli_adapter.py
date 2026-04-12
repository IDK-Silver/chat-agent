"""Tests for CLIAdapter slash command handling."""

import threading
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


def test_submit_input_allows_shell_command_while_turn_busy():
    adapter = CLIAdapter.__new__(CLIAdapter)
    adapter._agent = MagicMock()
    adapter._commands = MagicMock()
    adapter._commands.is_command.return_value = True
    adapter._commands.can_execute_while_processing.return_value = True
    adapter._commands._console = MagicMock()
    adapter._handle_command = MagicMock(return_value=False)
    adapter._turn_done = threading.Event()
    adapter._turn_done.clear()

    result = adapter.submit_input("/shell-status")

    assert result is False
    adapter._handle_command.assert_called_once_with("/shell-status")
    adapter._commands._console.print_warning.assert_not_called()


def test_compact_command_delegates_to_agent(tmp_path):
    adapter = CLIAdapter.__new__(CLIAdapter)
    adapter._agent = MagicMock()
    adapter._agent.run_manual_compact.return_value = 3
    adapter._commands = MagicMock()
    adapter._commands.execute.return_value = CommandResult.COMPACT
    adapter._commands._console = MagicMock()
    adapter._builder = MagicMock()
    adapter._workspace = MagicMock()
    adapter._agent_os_dir = tmp_path
    adapter._session_mgr = MagicMock()
    adapter._conversation = MagicMock()
    adapter._user_id = "u"
    adapter._display_name = "User"

    should_stop = adapter._handle_command("/compact")

    assert should_stop is False
    adapter._agent.run_manual_compact.assert_called_once_with()
    adapter._commands._console.print_info.assert_called_once_with(
        "Context compacted: 3 messages removed."
    )
