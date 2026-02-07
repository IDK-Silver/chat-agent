"""Tests for ContextBuilder.build_with_review()."""

from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation


class TestBuildWithReview:
    def test_appends_prefetch_to_system_prompt(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_review(
            conv,
            prefetch_results=["### Search\nfound: memory/file.md"],
            reminders=["Check time"],
        )

        system_content = messages[0].content
        assert "Base prompt" in system_content
        assert "## Pre-loaded Context" in system_content
        assert "found: memory/file.md" in system_content
        assert "## Reminders for This Response" in system_content
        assert "- Check time" in system_content

    def test_empty_prefetch_no_modification(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_review(conv, [], [])
        system_content = messages[0].content

        assert "Pre-loaded Context" not in system_content
        assert "Reminders" not in system_content

    def test_only_reminders(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_review(
            conv,
            prefetch_results=[],
            reminders=["Reminder 1", "Reminder 2"],
        )

        system_content = messages[0].content
        assert "Pre-loaded Context" not in system_content
        assert "## Reminders for This Response" in system_content
        assert "- Reminder 1" in system_content
        assert "- Reminder 2" in system_content

    def test_only_prefetch(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_review(
            conv,
            prefetch_results=["### Result\ndata here"],
            reminders=[],
        )

        system_content = messages[0].content
        assert "## Pre-loaded Context" in system_content
        assert "Reminders" not in system_content

    def test_no_system_prompt_unchanged(self):
        builder = ContextBuilder(system_prompt=None)
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_review(
            conv,
            prefetch_results=["data"],
            reminders=["note"],
        )

        # No system message, so nothing to append to
        assert messages[0].role == "user"

    def test_preserves_conversation_messages(self):
        builder = ContextBuilder(system_prompt="Base")
        conv = Conversation()
        conv.add("user", "msg1")
        conv.add("assistant", "resp1")
        conv.add("user", "msg2")

        messages = builder.build_with_review(
            conv,
            prefetch_results=["data"],
            reminders=[],
        )

        # system + 3 conversation messages
        assert len(messages) == 4
        assert messages[0].role == "system"
