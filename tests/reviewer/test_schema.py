"""Tests for reviewer schema models."""

import json

import pytest

from chat_agent.reviewer.schema import (
    PrefetchAction,
    PreReviewResult,
    RequiredAction,
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
        data = {
            "passed": True,
            "violations": [],
            "required_actions": [],
            "retry_instruction": "",
        }
        result = PostReviewResult.model_validate(data)
        assert result.passed is True
        assert result.violations == []

    def test_failed(self):
        data = {
            "passed": False,
            "violations": ["No grep before answering past event"],
            "required_actions": [
                {
                    "code": "grep_recall",
                    "description": "Search memory before recall response",
                    "tool": "execute_shell",
                    "command_must_contain": "grep",
                }
            ],
            "retry_instruction": "Use grep first.",
        }
        result = PostReviewResult.model_validate(data)
        assert result.passed is False
        assert len(result.violations) == 1
        assert len(result.required_actions) == 1
        assert result.required_actions[0].tool == "execute_shell"
        assert "grep" in result.retry_instruction

    def test_roundtrip_json(self):
        data = {
            "passed": False,
            "violations": ["v1", "v2"],
            "required_actions": [
                {
                    "code": "update_short_term",
                    "description": "Update short-term memory for topic shift",
                    "tool": "write_or_edit",
                    "target_path": "memory/short-term.md",
                }
            ],
            "retry_instruction": "Complete required actions.",
        }
        result = PostReviewResult.model_validate(data)
        dumped = json.loads(result.model_dump_json())
        assert dumped["passed"] is False
        assert dumped["violations"] == ["v1", "v2"]
        assert dumped["retry_instruction"] == "Complete required actions."
        assert dumped["required_actions"][0]["code"] == "update_short_term"
        assert dumped["required_actions"][0]["tool"] == "write_or_edit"

    def test_guidance_backward_compat(self):
        data = {
            "passed": False,
            "violations": ["legacy"],
            "guidance": "Legacy guidance field",
        }
        result = PostReviewResult.model_validate(data)
        assert result.guidance == "Legacy guidance field"
        assert result.required_actions == []
        assert result.retry_instruction == ""


class TestRequiredAction:
    def test_valid_action(self):
        action = RequiredAction(
            code="write_knowledge",
            description="Persist durable fact",
            tool="write_or_edit",
            target_path_glob="memory/agent/knowledge/*.md",
            index_path="memory/agent/knowledge/index.md",
        )
        assert action.tool == "write_or_edit"

    def test_valid_memory_edit_action(self):
        action = RequiredAction(
            code="persist_turn_memory",
            description="Persist rolling context",
            tool="memory_edit",
            target_path="memory/short-term.md",
        )
        assert action.tool == "memory_edit"
