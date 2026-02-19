"""Tests for ChatInput suspend/resume and CLIAdapter turn lifecycle."""

import threading
from unittest.mock import MagicMock

from chat_agent.cli.input import ChatInput


class TestChatInputSuspend:

    def test_suspend_sets_was_suspended_when_app_running(self):
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.app.is_running = True
        ci._session.app.current_buffer.text = ""
        # Pre-set ack so wait() doesn't block in test
        ci._suspended_ack.set()

        ci.suspend()

        assert ci._was_suspended is True
        ci._session.app.exit.assert_called_once_with(result="")

    def test_suspend_noop_when_app_not_running(self):
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.app.is_running = False

        ci.suspend()

        assert ci._was_suspended is False
        ci._session.app.exit.assert_not_called()

    def test_suspend_preserves_buffer_as_prefill(self):
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.app.is_running = True
        ci._session.app.current_buffer.text = "partial typing"
        ci._suspended_ack.set()

        ci.suspend()

        assert ci._prefill == "partial typing"

    def test_was_suspended_auto_resets(self):
        ci = ChatInput()
        ci._was_suspended = True

        assert ci.was_suspended is True
        assert ci.was_suspended is False

    def test_get_input_returns_empty_on_suspension(self):
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.prompt.return_value = ""
        ci._was_suspended = True

        result = ci.get_input()

        assert result == ""
        # ack event should be set so suspend() unblocks
        assert ci._suspended_ack.is_set()

    def test_get_input_suspension_checked_before_exit_requested(self):
        """Suspension takes precedence over exit_requested."""
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.prompt.return_value = ""
        ci._was_suspended = True
        ci._exit_requested = True

        result = ci.get_input()

        # Should return "" (suspended), not None (exit)
        assert result == ""
        # exit_requested NOT consumed; suspension was handled first
        assert ci._exit_requested is True


    def test_suspend_blocks_until_ack(self):
        """suspend() waits for get_input() to signal prompt has exited."""
        ci = ChatInput()
        ci._session = MagicMock()
        ci._session.app.is_running = True
        ci._session.app.current_buffer.text = ""

        finished = threading.Event()

        def run_suspend():
            ci.suspend()
            finished.set()

        t = threading.Thread(target=run_suspend)
        t.start()

        # suspend() should be blocked (ack not set yet)
        assert not finished.wait(timeout=0.1)

        # Simulate get_input() completing on the input thread
        ci._suspended_ack.set()
        assert finished.wait(timeout=1.0)
        t.join()


class TestCLIAdapterOnTurnStart:

    def _make_adapter(self):
        from chat_agent.agent.adapters.cli import CLIAdapter

        chat_input = MagicMock()
        adapter = CLIAdapter(
            chat_input=chat_input,
            console=MagicMock(),
            commands=MagicMock(),
            session_mgr=MagicMock(),
            conversation=MagicMock(),
            builder=MagicMock(),
            workspace=MagicMock(),
            agent_os_dir=MagicMock(),
            user_id="u",
            display_name="User",
            picker_fn=MagicMock(),
        )
        return adapter, chat_input

    def test_on_turn_start_non_cli_suspends(self):
        adapter, chat_input = self._make_adapter()

        adapter.on_turn_start("gmail")

        chat_input.suspend.assert_called_once()

    def test_on_turn_start_cli_does_not_suspend(self):
        adapter, chat_input = self._make_adapter()

        adapter.on_turn_start("cli")

        chat_input.suspend.assert_not_called()

    def test_on_turn_start_other_channel_suspends(self):
        adapter, chat_input = self._make_adapter()

        adapter.on_turn_start("line")

        chat_input.suspend.assert_called_once()
