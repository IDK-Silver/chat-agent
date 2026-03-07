"""Tests for config schema defaults and fields."""

import pytest
from pydantic import ValidationError

from chat_agent.core.schema import AgentConfig, AppConfig


def _ollama_llm() -> dict[str, object]:
    return {
        "provider": "ollama",
        "model": "test-model",
        "thinking": {"mode": "toggle", "enabled": False},
    }


def test_app_config_warn_on_failure_default_true():
    config = AppConfig.model_validate(
        {
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            }
        }
    )
    assert config.app.warn_on_failure is True
    assert config.app.timezone == "UTC+8"


def test_app_config_warn_on_failure_override_false():
    config = AppConfig.model_validate(
        {
            "app": {"warn_on_failure": False},
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            },
        }
    )
    assert config.app.warn_on_failure is False


def test_agent_config_enabled_default_true():
    config = AgentConfig.model_validate({"llm": _ollama_llm()})
    assert config.enabled is True


def test_agent_config_rejects_visible_text_review_mode():
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(
            {
                "llm": _ollama_llm(),
                "visible_text_review_mode": "all",
            }
        )


def test_discord_channel_config_defaults():
    config = AppConfig.model_validate(
        {
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            }
        }
    )
    discord_cfg = config.channels.discord
    assert discord_cfg.enabled is False
    assert discord_cfg.listen_dms is True
    assert discord_cfg.guild_review_interval_seconds == 60
    assert discord_cfg.auto_read_images is True
    assert discord_cfg.dm_debounce_seconds == 12
    assert discord_cfg.dm_max_wait_seconds == 180
    assert discord_cfg.dm_typing_quiet_seconds == 15
    assert discord_cfg.presence_mode == "auto"
    assert discord_cfg.presence_refresh_seconds == 90
    assert discord_cfg.presence_idle_after_seconds == 300


def test_terminal_tool_short_circuit_defaults():
    config = AppConfig.model_validate(
        {
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            }
        }
    )
    tcfg = config.tools.terminal_tool_short_circuit
    assert tcfg.enabled is True
    assert tcfg.allowed_tools == ["send_message", "schedule_action"]
    assert tcfg.schedule_action_allowed_actions == ["add", "remove"]


def test_terminal_tool_short_circuit_override():
    config = AppConfig.model_validate(
        {
            "tools": {
                "terminal_tool_short_circuit": {
                    "enabled": False,
                    "allowed_tools": ["send_message"],
                    "schedule_action_allowed_actions": ["add"],
                }
            },
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            },
        }
    )
    tcfg = config.tools.terminal_tool_short_circuit
    assert tcfg.enabled is False
    assert tcfg.allowed_tools == ["send_message"]
    assert tcfg.schedule_action_allowed_actions == ["add"]


def test_discord_channel_config_validates_ranges():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "channels": {
                    "discord": {
                        "send_delay_min": 5,
                        "send_delay_max": 1,
                    }
                },
                "agents": {
                    "brain": {
                        "llm": _ollama_llm(),
                    }
                },
            }
        )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "channels": {
                    "discord": {
                        "dm_debounce_seconds": 200,
                        "dm_max_wait_seconds": 100,
                    }
                },
                "agents": {
                    "brain": {
                        "llm": _ollama_llm(),
                    }
                },
            }
        )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "channels": {
                    "discord": {
                        "debounce_seconds": 20,
                        "max_wait_seconds": 10,
                    }
                },
                "agents": {
                    "brain": {
                        "llm": _ollama_llm(),
                    }
                },
            }
        )


@pytest.mark.parametrize("value", ["UTC+8", "UTC+08:00", "Asia/Taipei"])
def test_app_config_accepts_timezone_formats(value: str):
    config = AppConfig.model_validate(
        {
            "app": {"timezone": value},
            "agents": {
                "brain": {
                    "llm": _ollama_llm(),
                }
            },
        }
    )
    assert config.app.timezone == value


@pytest.mark.parametrize("value", ["UTC+25", "Taipei", "UTC+8:99"])
def test_app_config_rejects_invalid_timezone(value: str):
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "app": {"timezone": value},
                "agents": {
                    "brain": {
                        "llm": _ollama_llm(),
                    }
                },
            }
        )
