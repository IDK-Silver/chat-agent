"""Tests for ProgressReviewer."""

import json
from unittest.mock import MagicMock

from chat_agent.llm.schema import Message
from chat_agent.reviewer.progress_reviewer import ProgressReviewer


class TestProgressReviewer:
    def test_review_passed(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "block_instruction": "",
        })

        reviewer = ProgressReviewer(mock_client, "system prompt")
        result = reviewer.review(
            [Message(role="user", content="hello")],
            candidate_reply="intermediate chunk",
        )

        assert result is not None
        assert result.passed is True
        assert result.violations == []

    def test_review_retries_on_parse_failure(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "not json",
            json.dumps({
                "passed": False,
                "violations": ["simulated_user_turn"],
                "block_instruction": "rewrite",
            }),
        ]

        reviewer = ProgressReviewer(mock_client, "system prompt", parse_retries=1)
        result = reviewer.review(
            [Message(role="user", content="hello")],
            candidate_reply="chunk",
        )

        assert result is not None
        assert result.passed is False
        assert result.violations == ["simulated_user_turn"]
        assert mock_client.chat.call_count == 2

    def test_review_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = TimeoutError("Timeout")

        reviewer = ProgressReviewer(mock_client, "system prompt")
        result = reviewer.review(
            [Message(role="user", content="hello")],
            candidate_reply="chunk",
        )

        assert result is None
        assert reviewer.last_error == "Timeout"

    def test_review_builds_progress_packet(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "passed": True,
            "violations": [],
            "block_instruction": "",
        })

        reviewer = ProgressReviewer(mock_client, "system prompt")
        reviewer.review(
            [Message(role="user", content="latest user")],
            candidate_reply="candidate text",
        )

        sent_messages = mock_client.chat.call_args[0][0]
        assert sent_messages[0].role == "system"
        assert sent_messages[1].role == "user"
        payload = sent_messages[1].content or ""
        assert "PROGRESS_REVIEW_PACKET_JSON" in payload
        assert "candidate text" in payload
