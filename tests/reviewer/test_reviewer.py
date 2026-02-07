"""Tests for PreReviewer and PostReviewer."""

import json
from unittest.mock import MagicMock

import pytest

from chat_agent.core.schema import AgentConfig, OllamaConfig
from chat_agent.llm.schema import Message, ToolCall, ToolDefinition, ToolParameter
from chat_agent.reviewer.pre_reviewer import PreReviewer
from chat_agent.reviewer.post_reviewer import PostReviewer
from chat_agent.tools import ToolRegistry


def _make_agent_config(**overrides):
    defaults = {
        "llm": {"provider": "ollama", "model": "test-model"},
    }
    defaults.update(overrides)
    return AgentConfig.model_validate(defaults)


def _make_registry():
    """Create a registry with mock tools for testing."""
    registry = ToolRegistry()
    registry.register(
        "read_file",
        lambda path: f"Content of {path}",
        ToolDefinition(
            name="read_file",
            description="Read file",
            parameters={"path": ToolParameter(type="string", description="Path")},
            required=["path"],
        ),
    )
    registry.register(
        "execute_shell",
        lambda command: f"memory/short-term.md:1:some content\nmemory/agent/persona.md:5:other content",
        ToolDefinition(
            name="execute_shell",
            description="Shell",
            parameters={"command": ToolParameter(type="string", description="Cmd")},
            required=["command"],
        ),
    )
    registry.register(
        "get_current_time",
        lambda timezone="UTC": "2026-02-07 14:30",
        ToolDefinition(
            name="get_current_time",
            description="Time",
            parameters={"timezone": ToolParameter(type="string", description="TZ")},
        ),
    )
    return registry


class TestPreReviewer:
    @pytest.fixture
    def config(self):
        return _make_agent_config(
            max_prefetch_actions=3,
            max_files_per_grep=2,
            shell_whitelist=["grep", "cat", "ls"],
        )

    @pytest.fixture
    def registry(self):
        return _make_registry()

    def test_review_returns_result(self, config, registry):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "triggered_rules": ["past event"],
            "prefetch": [
                {
                    "tool": "execute_shell",
                    "arguments": {"command": "grep -rn 'test' memory/"},
                    "reason": "Search memory",
                }
            ],
            "reminders": ["Use search results"],
        })

        reviewer = PreReviewer(mock_client, "system prompt", registry, config)
        messages = [Message(role="user", content="Remember what we talked about?")]
        result = reviewer.review(messages)

        assert result is not None
        assert len(result.triggered_rules) == 1
        assert len(result.prefetch) == 1
        assert len(result.reminders) == 1

    def test_review_returns_none_on_invalid_json(self, config, registry):
        mock_client = MagicMock()
        mock_client.chat.return_value = "This is not JSON"

        reviewer = PreReviewer(mock_client, "system prompt", registry, config)
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_returns_none_on_exception(self, config, registry):
        mock_client = MagicMock()
        mock_client.chat.side_effect = ConnectionError("Server down")

        reviewer = PreReviewer(mock_client, "system prompt", registry, config)
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_handles_markdown_code_block(self, config, registry):
        mock_client = MagicMock()
        mock_client.chat.return_value = '```json\n{"triggered_rules": [], "prefetch": [], "reminders": []}\n```'

        reviewer = PreReviewer(mock_client, "system prompt", registry, config)
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.triggered_rules == []

    def test_execute_prefetch_grep_with_expansion(self, config, registry):
        mock_client = MagicMock()
        reviewer = PreReviewer(mock_client, "system prompt", registry, config)

        from chat_agent.reviewer.schema import PreReviewResult, PrefetchAction

        result = PreReviewResult(
            triggered_rules=["test"],
            prefetch=[
                PrefetchAction(
                    tool="execute_shell",
                    arguments={"command": "grep -rn 'keyword' memory/"},
                    reason="Search memory",
                )
            ],
            reminders=[],
        )

        outputs = reviewer.execute_prefetch(result)
        # Should have grep result + up to 2 auto-loaded files (max_files_per_grep=2)
        assert len(outputs) >= 1
        assert any("Search memory" in o for o in outputs)
        # Auto-expanded files
        assert any("[Auto-loaded]" in o for o in outputs)

    def test_execute_prefetch_respects_max_actions(self, config, registry):
        mock_client = MagicMock()
        reviewer = PreReviewer(mock_client, "system prompt", registry, config)

        from chat_agent.reviewer.schema import PreReviewResult, PrefetchAction

        actions = [
            PrefetchAction(
                tool="read_file",
                arguments={"path": f"memory/file{i}.md"},
                reason=f"Read file {i}",
            )
            for i in range(10)
        ]
        result = PreReviewResult(
            triggered_rules=["test"],
            prefetch=actions,
            reminders=[],
        )

        outputs = reviewer.execute_prefetch(result)
        # max_prefetch_actions=3, so only 3 actions executed
        assert len(outputs) == 3

    def test_blocked_shell_command(self, config, registry):
        mock_client = MagicMock()
        reviewer = PreReviewer(mock_client, "system prompt", registry, config)

        from chat_agent.reviewer.schema import PreReviewResult, PrefetchAction

        result = PreReviewResult(
            triggered_rules=["test"],
            prefetch=[
                PrefetchAction(
                    tool="execute_shell",
                    arguments={"command": "rm -rf memory/"},
                    reason="Bad command",
                )
            ],
            reminders=[],
        )

        outputs = reviewer.execute_prefetch(result)
        assert len(outputs) == 0

    def test_allowed_commands(self, config, registry):
        reviewer = PreReviewer(MagicMock(), "", registry, config)
        assert reviewer._is_allowed_command("grep -r 'test' memory/")
        assert reviewer._is_allowed_command("cat memory/file.md")
        assert reviewer._is_allowed_command("ls memory/")
        assert not reviewer._is_allowed_command("rm -rf /")
        assert not reviewer._is_allowed_command("python script.py")
        assert not reviewer._is_allowed_command("")


class TestPostReviewer:
    def test_review_passed(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "guidance": "",
        })

        reviewer = PostReviewer(mock_client, "system prompt")
        messages = [Message(role="user", content="hello")]
        result = reviewer.review(messages)

        assert result is not None
        assert result.passed is True

    def test_review_failed(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": False,
            "violations": ["No grep before answering"],
            "guidance": "Search memory first",
        })

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="remember?")])

        assert result is not None
        assert result.passed is False
        assert len(result.violations) == 1

    def test_review_returns_none_on_invalid_json(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = "Not valid JSON at all"

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = TimeoutError("Timeout")

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_handles_markdown_code_block(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '```json\n{"passed": true, "violations": [], "guidance": ""}\n```'
        )

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.passed is True

    def test_review_extracts_json_from_reasoning_text(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            "Analysis text before JSON.\n\n"
            "```json\n"
            '{"passed": false, "violations": ["missing tool call"], "guidance": "retry"}\n'
            "```"
        )

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.passed is False
        assert result.violations == ["missing tool call"]
        assert result.guidance == "retry"

    def test_review_strips_system_messages(self):
        """Reviewer should see conversation without the original system message."""
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True, "violations": [], "guidance": "",
        })

        reviewer = PostReviewer(mock_client, "review system prompt")
        messages = [
            Message(role="system", content="original system prompt"),
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi there"),
        ]
        reviewer.review(messages)

        # Check that the reviewer's system message is replaced, not original
        call_args = mock_client.chat.call_args[0][0]
        assert call_args[0].role == "system"
        assert call_args[0].content == "review system prompt"
        # Original system message should be stripped
        assert all(m.content != "original system prompt" for m in call_args)
