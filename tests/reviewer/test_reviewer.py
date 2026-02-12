"""Tests for PostReviewer."""

import json
from unittest.mock import MagicMock

import httpx

from chat_agent.llm.schema import Message
from chat_agent.reviewer.post_reviewer import PostReviewer
from chat_agent.reviewer.review_packet import ReviewPacket


class TestPostReviewer:
    def test_review_passed(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
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
            "required_actions": [
                {
                    "code": "grep_recall",
                    "description": "Search memory before answering",
                    "tool": "execute_shell",
                    "command_must_contain": "grep",
                }
            ],
            "retry_instruction": "Search memory first",
        })

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="remember?")])

        assert result is not None
        assert result.passed is False
        assert len(result.violations) == 1
        assert len(result.required_actions) == 1
        assert result.required_actions[0].tool == "execute_shell"

    def test_review_returns_none_on_invalid_json(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = "Not valid JSON at all"

        reviewer = PostReviewer(mock_client, "system prompt", parse_retries=0)
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_retries_on_parse_failure(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "[Tool calls: write_file(path=memory/agent/short-term.md)]",
            json.dumps({
                "passed": True,
                "violations": [],
                "required_actions": [],
                "retry_instruction": "",
            }),
        ]

        reviewer = PostReviewer(mock_client, "system prompt", parse_retries=1)
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.passed is True
        assert mock_client.chat.call_count == 2

    def test_review_uses_custom_parse_retry_prompt(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "not json",
            json.dumps({
                "passed": True,
                "violations": [],
                "required_actions": [],
                "retry_instruction": "",
            }),
        ]

        reviewer = PostReviewer(
            mock_client,
            "system prompt",
            parse_retries=1,
            parse_retry_prompt="CUSTOM PARSE RETRY PROMPT",
        )
        reviewer.review([Message(role="user", content="hi")])

        # Second call includes injected custom retry prompt
        second_call_messages = mock_client.chat.call_args_list[1][0][0]
        assert second_call_messages[-1].role == "user"
        assert second_call_messages[-1].content == "CUSTOM PARSE RETRY PROMPT"

    def test_no_warning_for_intermediate_parse_failure(self, caplog):
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "not json",
            json.dumps({
                "passed": True,
                "violations": [],
                "required_actions": [],
                "retry_instruction": "",
            }),
        ]
        reviewer = PostReviewer(mock_client, "system prompt", parse_retries=1)

        with caplog.at_level("WARNING"):
            result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert all(
            "Failed to parse post-review response" not in rec.message
            for rec in caplog.records
        )

    def test_review_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = TimeoutError("Timeout")

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None

    def test_review_captures_http_error_detail(self):
        request = httpx.Request(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        response = httpx.Response(
            404,
            request=request,
            json={
                "error": {
                    "message": "No endpoints found matching your data policy",
                    "code": 404,
                }
            },
        )
        err = httpx.HTTPStatusError("not found", request=request, response=response)
        mock_client = MagicMock()
        mock_client.chat.side_effect = err

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is None
        assert reviewer.last_error is not None
        assert "HTTP 404" in reviewer.last_error
        assert "data policy" in reviewer.last_error

    def test_review_handles_markdown_code_block(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '```json\n{"passed": true, "violations": [], "required_actions": [], "retry_instruction": ""}\n```'
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
            '{"passed": false, "violations": ["missing tool call"], '
            '"required_actions": [{"code":"x","description":"y","tool":"get_current_time"}], '
            '"retry_instruction": "retry"}\n'
            "```"
        )

        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.passed is False
        assert result.violations == ["missing tool call"]
        assert result.retry_instruction == "retry"

    def test_review_parses_target_signals(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
            "target_signals": [
                {
                    "signal": "target_persona",
                    "reason": "Name contract changed",
                }
            ],
        })
        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert len(result.target_signals) == 1
        assert result.target_signals[0].signal == "target_persona"
        assert result.target_signals[0].requires_persistence is True

    def test_review_defaults_target_and_anomaly_signals_when_missing(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
        })
        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert result.target_signals == []
        assert result.anomaly_signals == []

    def test_review_parses_anomaly_signals(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": False,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
            "target_signals": [],
            "anomaly_signals": [
                {
                    "signal": "anomaly_missing_required_target",
                    "target_signal": "target_short_term",
                    "reason": "missing short-term write",
                }
            ],
        })
        reviewer = PostReviewer(mock_client, "system prompt")
        result = reviewer.review([Message(role="user", content="hi")])

        assert result is not None
        assert len(result.anomaly_signals) == 1
        assert result.anomaly_signals[0].signal == "anomaly_missing_required_target"
        assert result.anomaly_signals[0].target_signal == "target_short_term"

    def test_review_can_use_review_packet_input(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
            "target_signals": [],
            "anomaly_signals": [],
        })
        reviewer = PostReviewer(mock_client, "system prompt")
        packet = ReviewPacket(
            latest_user_turn="hi",
            candidate_assistant_reply="hello",
        )
        result = reviewer.review(
            [Message(role="user", content="ignored when packet present")],
            review_packet=packet,
        )

        assert result is not None
        sent_messages = mock_client.chat.call_args[0][0]
        assert sent_messages[-1].role == "user"
        assert "POST_REVIEW_PACKET_JSON" in (sent_messages[-1].content or "")

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
