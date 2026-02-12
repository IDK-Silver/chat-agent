"""Tests for reviewer schema models."""

import json

from chat_agent.reviewer.schema import (
    RequiredAction,
    PostReviewResult,
)


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
                    "target_path": "memory/agent/short-term.md",
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
            target_path="memory/agent/short-term.md",
        )
        assert action.tool == "memory_edit"
