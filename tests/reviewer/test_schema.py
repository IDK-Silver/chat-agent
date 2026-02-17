"""Tests for reviewer schema models."""

import json

import pytest
from pydantic import ValidationError

from chat_agent.reviewer.schema import (
    RequiredAction,
    PostReviewResult,
)


class TestPostReviewResult:
    def test_passed(self):
        data = {
            "passed": True,
            "required_actions": [],
            "retry_instruction": "",
        }
        result = PostReviewResult.model_validate(data)
        assert result.passed is True
        assert result.required_actions == []

    def test_failed(self):
        data = {
            "passed": False,
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
        assert len(result.required_actions) == 1
        assert result.required_actions[0].tool == "execute_shell"
        assert "grep" in result.retry_instruction

    def test_roundtrip_json(self):
        data = {
            "passed": False,
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
        assert dumped["retry_instruction"] == "Complete required actions."
        assert dumped["required_actions"][0]["code"] == "update_short_term"
        assert dumped["required_actions"][0]["tool"] == "write_or_edit"

    def test_guidance_optional(self):
        data = {
            "passed": False,
            "required_actions": [],
            "guidance": "Legacy guidance field",
        }
        result = PostReviewResult.model_validate(data)
        assert result.guidance == "Legacy guidance field"
        assert result.required_actions == []
        assert result.retry_instruction == ""

    def test_rejects_extra_fields(self):
        data = {
            "passed": True,
            "required_actions": [],
            "retry_instruction": "",
            "violations": [],
        }
        with pytest.raises(ValidationError):
            PostReviewResult.model_validate(data)


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
