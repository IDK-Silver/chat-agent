"""Tests for context refresh: config, sentinel, timer, boot cache, and refresh logic."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.schema import InboundMessage, RefreshSentinel, ShutdownSentinel
from chat_agent.context import ContextBuilder, Conversation
from chat_agent.core.schema import ContextRefreshConfig


# ── Config schema ──────────────────────────────────────────────────────


class TestContextRefreshConfig:
    def test_defaults(self):
        cfg = ContextRefreshConfig()
        assert cfg.enabled is True
        assert cfg.interval_hours == 6
        assert cfg.on_day_change is True
        assert cfg.preserve_turns == 2

    def test_preserve_turns_zero_allowed(self):
        cfg = ContextRefreshConfig(preserve_turns=0)
        assert cfg.preserve_turns == 0

    def test_interval_hours_minimum(self):
        with pytest.raises(Exception):
            ContextRefreshConfig(interval_hours=0)

    def test_custom_values(self):
        cfg = ContextRefreshConfig(
            enabled=False,
            interval_hours=12,
            on_day_change=False,
            preserve_turns=5,
        )
        assert cfg.enabled is False
        assert cfg.interval_hours == 12
        assert cfg.on_day_change is False
        assert cfg.preserve_turns == 5


# ── RefreshSentinel in queue ───────────────────────────────────────────


class TestRefreshSentinelQueue:
    def test_refresh_sentinel_lowest_priority(self, tmp_path):
        """RefreshSentinel should have lower priority than InboundMessage."""
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(RefreshSentinel())
        q.put(InboundMessage(channel="cli", content="hi", priority=5, sender="u"))

        # InboundMessage (priority=5) should come before RefreshSentinel (priority=999)
        msg1, _ = q.get()
        msg2, _ = q.get()
        assert isinstance(msg1, InboundMessage)
        assert isinstance(msg2, RefreshSentinel)

    def test_refresh_sentinel_not_persisted(self, tmp_path):
        """RefreshSentinel should not create files on disk."""
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(RefreshSentinel())

        pending_files = list((tmp_path / "q" / "pending").iterdir())
        assert len(pending_files) == 0

    def test_shutdown_before_refresh(self, tmp_path):
        """ShutdownSentinel (-1) should come before RefreshSentinel (999)."""
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(RefreshSentinel())
        q.put(ShutdownSentinel(graceful=True))

        msg1, _ = q.get()
        assert isinstance(msg1, ShutdownSentinel)


# ── RefreshTimer ───────────────────────────────────────────────────────


class TestRefreshTimer:
    def test_interval_elapsed_triggers(self, tmp_path):
        """Timer should enqueue RefreshSentinel when interval_hours has elapsed."""
        from chat_agent.agent.core import _RefreshTimer
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        cfg = ContextRefreshConfig(interval_hours=1, on_day_change=False)
        timer = _RefreshTimer(q, cfg)

        # Simulate time passing beyond interval
        timer._last_refresh = datetime.now() - timedelta(hours=2)
        timer._loop_once()

        msg, _ = q.get()
        assert isinstance(msg, RefreshSentinel)

    def test_day_change_triggers(self, tmp_path):
        """Timer should enqueue RefreshSentinel when date changes."""
        from chat_agent.agent.core import _RefreshTimer
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        cfg = ContextRefreshConfig(interval_hours=24, on_day_change=True)
        timer = _RefreshTimer(q, cfg)

        # Simulate yesterday
        timer._last_date = (datetime.now() - timedelta(days=1)).date()
        timer._loop_once()

        msg, _ = q.get()
        assert isinstance(msg, RefreshSentinel)

    def test_no_trigger_when_not_due(self, tmp_path):
        """Timer should not enqueue when conditions are not met."""
        from chat_agent.agent.core import _RefreshTimer
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        cfg = ContextRefreshConfig(interval_hours=6, on_day_change=True)
        timer = _RefreshTimer(q, cfg)

        # Just created, no time passed
        timer._loop_once()

        assert q.pending_count() == 0

    def test_mark_refreshed_resets_timers(self, tmp_path):
        """mark_refreshed should update both last_refresh and last_date."""
        from chat_agent.agent.core import _RefreshTimer
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        cfg = ContextRefreshConfig(interval_hours=1)
        timer = _RefreshTimer(q, cfg)

        # Force old state
        timer._last_refresh = datetime.now() - timedelta(hours=10)
        timer._last_date = (datetime.now() - timedelta(days=1)).date()

        timer.mark_refreshed()

        assert (datetime.now() - timer._last_refresh).total_seconds() < 2
        assert timer._last_date == datetime.now().date()


# ── Boot files cache ──────────────────────────────────────────────────


class TestBootFilesCache:
    def test_build_uses_cached_boot_content(self, tmp_path):
        """After reload_boot_files, build() should use cached content, not re-read disk."""
        mem_dir = tmp_path / "memory" / "agent"
        mem_dir.mkdir(parents=True)
        (mem_dir / "persona.md").write_text("original", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
        )
        builder.reload_boot_files()

        # Modify file on disk after reload
        (mem_dir / "persona.md").write_text("modified", encoding="utf-8")

        conv = Conversation()
        conv.add("user", "hi")
        messages = builder.build(conv)

        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "original" in boot_msgs[0].content
        assert "modified" not in boot_msgs[0].content

    def test_reload_picks_up_disk_changes(self, tmp_path):
        """Calling reload_boot_files() again should pick up disk changes."""
        mem_dir = tmp_path / "memory" / "agent"
        mem_dir.mkdir(parents=True)
        (mem_dir / "persona.md").write_text("v1", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
        )
        builder.reload_boot_files()

        # Modify and reload
        (mem_dir / "persona.md").write_text("v2", encoding="utf-8")
        builder.reload_boot_files()

        conv = Conversation()
        conv.add("user", "hi")
        messages = builder.build(conv)

        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert "v2" in boot_msgs[0].content

    def test_no_boot_content_without_reload(self, tmp_path):
        """Without calling reload_boot_files, build() should not inject boot context."""
        mem_dir = tmp_path / "memory" / "agent"
        mem_dir.mkdir(parents=True)
        (mem_dir / "persona.md").write_text("data", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
        )
        # Intentionally not calling reload_boot_files()

        conv = Conversation()
        conv.add("user", "hi")
        messages = builder.build(conv)

        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 0

    def test_update_system_prompt(self):
        """update_system_prompt should replace the system prompt in build output."""
        builder = ContextBuilder(system_prompt="old prompt")
        builder.update_system_prompt("new prompt")

        conv = Conversation()
        conv.add("user", "hi")
        messages = builder.build(conv)

        assert messages[0].content == "new prompt"


# ── Context refresh in run loop ───────────────────────────────────────


class TestRunLoopRefreshSentinel:
    def test_refresh_sentinel_triggers_refresh(self, tmp_path):
        """RefreshSentinel in queue should trigger _perform_context_refresh."""
        from chat_agent.agent.core import AgentCore
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        # Only RefreshSentinel first; shutdown is enqueued by the mock
        q.put(RefreshSentinel())

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core._context_refresh_config = ContextRefreshConfig()
        core._refresh_timer = None
        core.graceful_exit = MagicMock()

        def _fake_refresh():
            q.put(ShutdownSentinel(graceful=False))

        core._perform_context_refresh = _fake_refresh

        core.run()
        # If we reach here, refresh was called (it enqueued shutdown)

    def test_refresh_sentinel_skipped_when_queue_busy(self, tmp_path):
        """RefreshSentinel should be skipped when queue has pending messages."""
        from chat_agent.agent.core import AgentCore
        from chat_agent.agent.queue import PersistentPriorityQueue

        q = PersistentPriorityQueue(tmp_path / "q")
        # InboundMessage (priority=0) comes before RefreshSentinel (priority=999)
        msg = InboundMessage(channel="cli", content="hi", priority=0, sender="u")
        q.put(msg)
        q.put(RefreshSentinel())

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core._context_refresh_config = ContextRefreshConfig()
        core._refresh_timer = None
        core._perform_context_refresh = MagicMock()
        core.graceful_exit = MagicMock()

        processed = []

        def fake_process(m, receipt):
            processed.append(m.content)
            # After processing the message, there's still RefreshSentinel in queue
            # but we also add shutdown to eventually exit
            q.put(ShutdownSentinel(graceful=False))

        core._process_inbound = fake_process

        core.run()
        assert processed == ["hi"]
        # RefreshSentinel skipped because queue had the shutdown pending
        core._perform_context_refresh.assert_not_called()


# ── _perform_context_refresh ──────────────────────────────────────────


class TestPerformContextRefresh:
    def test_compacts_conversation(self, tmp_path):
        """Refresh should compact conversation to preserve_turns."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=1)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = None
        core.conversation = Conversation()
        core.workspace = MagicMock()
        core.workspace.get_system_prompt.side_effect = FileNotFoundError
        core.builder = MagicMock()
        core.session_mgr = None
        core.console = MagicMock()
        core.agent_os_dir = tmp_path

        # Add 3 turns
        for i in range(3):
            core.conversation.add("user", f"msg{i}")
            core.conversation.add("assistant", f"resp{i}")

        assert len(core.conversation.get_messages()) == 6

        core._perform_context_refresh()

        # Only 1 turn preserved (2 messages)
        assert len(core.conversation.get_messages()) == 2

    def test_reloads_boot_files(self, tmp_path):
        """Refresh should call builder.reload_boot_files()."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=0)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = None
        core.conversation = Conversation()
        core.workspace = MagicMock()
        core.workspace.get_system_prompt.return_value = "prompt {agent_os_dir}"
        core.builder = MagicMock()
        core.session_mgr = None
        core.console = MagicMock()
        core.agent_os_dir = tmp_path

        core._perform_context_refresh()

        core.builder.reload_boot_files.assert_called_once()

    def test_updates_system_prompt(self, tmp_path):
        """Refresh should re-resolve system prompt with agent_os_dir."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=0)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = None
        core.conversation = Conversation()
        core.workspace = MagicMock()
        core.workspace.get_system_prompt.return_value = "path={agent_os_dir}"
        core.builder = MagicMock()
        core.session_mgr = None
        core.console = MagicMock()
        core.agent_os_dir = tmp_path

        core._perform_context_refresh()

        core.builder.update_system_prompt.assert_called_once_with(
            f"path={tmp_path}"
        )

    def test_rotates_session(self, tmp_path):
        """Refresh should finalize old session and create new one."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=0)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = None
        core.conversation = Conversation()
        core.workspace = MagicMock()
        core.workspace.get_system_prompt.return_value = "prompt"
        core.builder = MagicMock()
        core.session_mgr = MagicMock()
        core.console = MagicMock()
        core.agent_os_dir = tmp_path
        core.user_id = "test_user"
        core.display_name = "Test"

        core._perform_context_refresh()

        core.session_mgr.finalize.assert_called_once_with("refreshed")
        core.session_mgr.create.assert_called_once_with("test_user", "Test")

    def test_marks_timer_refreshed(self, tmp_path):
        """Refresh should call mark_refreshed on the timer."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=0)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = MagicMock()
        core.conversation = Conversation()
        core.workspace = MagicMock()
        core.workspace.get_system_prompt.return_value = "prompt"
        core.builder = MagicMock()
        core.session_mgr = None
        core.console = MagicMock()
        core.agent_os_dir = tmp_path

        core._perform_context_refresh()

        core._refresh_timer.mark_refreshed.assert_called_once()

    def test_exception_does_not_propagate(self, tmp_path):
        """Refresh errors should be swallowed (logged, not raised)."""
        from chat_agent.agent.core import AgentCore

        cfg = ContextRefreshConfig(preserve_turns=0)

        core = AgentCore.__new__(AgentCore)
        core._context_refresh_config = cfg
        core._refresh_timer = None
        core.conversation = MagicMock()
        core.conversation.compact.side_effect = RuntimeError("boom")
        core.workspace = MagicMock()
        core.builder = MagicMock()
        core.session_mgr = None
        core.console = MagicMock()
        core.agent_os_dir = tmp_path

        # Should not raise
        core._perform_context_refresh()
