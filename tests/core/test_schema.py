"""Tests for config schema defaults and fields."""

import pytest
from pydantic import ValidationError

from chat_agent.core.schema import AgentConfig, AppConfig


def test_app_config_warn_on_failure_default_true():
    config = AppConfig.model_validate(
        {
            "agents": {
                "brain": {
                    "llm": {"provider": "ollama", "model": "test-model"},
                }
            }
        }
    )
    assert config.warn_on_failure is True


def test_app_config_warn_on_failure_override_false():
    config = AppConfig.model_validate(
        {
            "warn_on_failure": False,
            "agents": {
                "brain": {
                    "llm": {"provider": "ollama", "model": "test-model"},
                }
            },
        }
    )
    assert config.warn_on_failure is False


def test_agent_config_enabled_default_true():
    config = AgentConfig.model_validate(
        {"llm": {"provider": "ollama", "model": "test-model"}}
    )
    assert config.enabled is True


def test_agent_config_rejects_visible_text_review_mode():
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(
            {
                "llm": {"provider": "ollama", "model": "test-model"},
                "visible_text_review_mode": "all",
            }
        )
