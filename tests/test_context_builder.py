"""Tests for ContextBuilder.build_with_reminders()."""

from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation


class TestBuildWithReminders:
    def test_appends_reminders_to_system_prompt(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, ["Check time", "Be concise"])

        system_content = messages[0].content
        assert "Base prompt" in system_content
        assert "## Reminders for This Response" in system_content
        assert "- Check time" in system_content
        assert "- Be concise" in system_content

    def test_empty_reminders_no_modification(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, [])
        system_content = messages[0].content

        assert "Reminders" not in system_content

    def test_no_system_prompt_unchanged(self):
        builder = ContextBuilder(system_prompt=None)
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, ["note"])

        # No system message, so nothing to append to
        assert messages[0].role == "user"

    def test_preserves_conversation_messages(self):
        builder = ContextBuilder(system_prompt="Base")
        conv = Conversation()
        conv.add("user", "msg1")
        conv.add("assistant", "resp1")
        conv.add("user", "msg2")

        messages = builder.build_with_reminders(conv, ["reminder"])

        # system + 3 conversation messages
        assert len(messages) == 4
        assert messages[0].role == "system"
