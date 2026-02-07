"""Tests for reviewer schema models."""

import json

import pytest

from chat_agent.reviewer.schema import (
    PrefetchAction,
    PreReviewResult,
    PostReviewResult,
)


class TestPrefetchAction:
    def test_valid_read_file(self):
        action = PrefetchAction(
            tool="read_file",
            arguments={"path": "memory/short-term.md"},
            reason="Load recent context",
        )
        assert action.tool == "read_file"
        assert action.arguments["path"] == "memory/short-term.md"

    def test_valid_execute_shell(self):
        action = PrefetchAction(
            tool="execute_shell",
            arguments={"command": "grep -r 'keyword' memory/"},
            reason="Search memory",
        )
        assert action.tool == "execute_shell"

    def test_invalid_tool_rejected(self):
        with pytest.raises(ValueError):
            PrefetchAction(
                tool="write_file",
                arguments={"path": "x"},
                reason="Should fail",
            )


class TestPreReviewResult:
    def test_from_json(self):
        data = {
            "triggered_rules": ["past event reference"],
            "prefetch": [
                {
                    "tool": "execute_shell",
                    "arguments": {"command": "grep -rn 'test' memory/"},
                    "reason": "Search for test",
                }
            ],
            "reminders": ["Use search results in response"],
        }
        result = PreReviewResult.model_validate(data)
        assert len(result.triggered_rules) == 1
        assert len(result.prefetch) == 1
        assert result.prefetch[0].tool == "execute_shell"
        assert len(result.reminders) == 1

    def test_empty_result(self):
        data = {"triggered_rules": [], "prefetch": [], "reminders": []}
        result = PreReviewResult.model_validate(data)
        assert result.triggered_rules == []
        assert result.prefetch == []

    def test_roundtrip_json(self):
        data = {
            "triggered_rules": ["rule1"],
            "prefetch": [
                {
                    "tool": "get_current_time",
                    "arguments": {"timezone": "Asia/Taipei"},
                    "reason": "Time check",
                }
            ],
            "reminders": ["Check time before responding"],
        }
        result = PreReviewResult.model_validate(data)
        dumped = json.loads(result.model_dump_json())
        assert dumped == data


class TestPostReviewResult:
    def test_passed(self):
        data = {"passed": True, "violations": [], "guidance": ""}
        result = PostReviewResult.model_validate(data)
        assert result.passed is True
        assert result.violations == []

    def test_failed(self):
        data = {
            "passed": False,
            "violations": ["No grep before answering past event"],
            "guidance": "Search memory with grep first",
        }
        result = PostReviewResult.model_validate(data)
        assert result.passed is False
        assert len(result.violations) == 1
        assert "grep" in result.guidance

    def test_roundtrip_json(self):
        data = {
            "passed": False,
            "violations": ["v1", "v2"],
            "guidance": "Fix these issues",
        }
        result = PostReviewResult.model_validate(data)
        dumped = json.loads(result.model_dump_json())
        assert dumped == data
