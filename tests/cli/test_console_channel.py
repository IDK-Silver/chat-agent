"""Tests for ChatConsole channel display methods."""

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from chat_agent.cli.console import ChatConsole


def _make_console(user_id="yufeng"):
    """Create a ChatConsole with captured output."""
    c = ChatConsole()
    c.set_current_user(user_id)
    buf = StringIO()
    c.console = Console(file=buf, width=60, force_terminal=True)
    return c, buf


class TestFormatChannelLabel:
    def test_own_user(self):
        c, _ = _make_console(user_id="yufeng")
        label = c._format_channel_label("cli", "yufeng")
        assert "cli" in label
        # Should NOT show sender when it's the current user
        assert "yufeng" not in label

    def test_other_sender(self):
        c, _ = _make_console(user_id="yufeng")
        label = c._format_channel_label("line", "friend")
        assert "line" in label
        assert "friend" in label

    def test_no_sender(self):
        c, _ = _make_console(user_id="yufeng")
        label = c._format_channel_label("system", None)
        assert "system" in label


class TestPrintInbound:
    def test_prints_content(self):
        c, buf = _make_console()
        c.print_inbound("cli", "yufeng", "hello world")
        output = buf.getvalue()
        assert "hello world" in output
        assert "received" in output.lower() or "cli" in output

    def test_prints_channel(self):
        c, buf = _make_console()
        c.print_inbound("line", "friend", "hi")
        output = buf.getvalue()
        assert "line" in output


class TestPrintProcessing:
    def test_prints_channel(self):
        c, buf = _make_console()
        c.print_processing("cli", "yufeng")
        output = buf.getvalue()
        assert "processing" in output.lower() or "cli" in output


class TestPrintOutbound:
    def test_prints_content(self):
        c, buf = _make_console()
        c.print_outbound("cli", "yufeng", "response text")
        output = buf.getvalue()
        assert "response" in output.lower()

    def test_empty_content_skipped(self):
        c, buf = _make_console()
        c.print_outbound("cli", "yufeng", "")
        output = buf.getvalue()
        # Should be empty or minimal (no "response" header)
        assert "response" not in output.lower() or output.strip() == ""

    def test_none_content_skipped(self):
        c, buf = _make_console()
        c.print_outbound("cli", "yufeng", None)
        output = buf.getvalue()
        assert "response" not in output.lower() or output.strip() == ""
