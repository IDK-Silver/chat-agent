import os
from pathlib import Path
from typing import TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import TypeAdapter

from .schema import (
    AnthropicConfig,
    AppConfig,
    GeminiConfig,
    LLMConfig,
    OllamaConfig,
    OpenAIConfig,
    OpenRouterConfig,
)
from ..llm.reasoning import validate_and_normalize_reasoning_config

load_dotenv()

CFGS_DIR = Path(__file__).parent.parent.parent.parent / "cfgs"

T = TypeVar(
    "T",
    OllamaConfig,
    OpenAIConfig,
    AnthropicConfig,
    GeminiConfig,
    OpenRouterConfig,
)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _resolve_api_key(config: T) -> T:
    """Resolve api_key from environment variable if api_key_env is set."""
    if not hasattr(config, "api_key_env") or config.api_key_env is None:
        return config

    api_key = os.getenv(config.api_key_env)
    return config.model_copy(update={"api_key": api_key, "api_key_env": None})


def resolve_llm_config(llm_path: str) -> LLMConfig:
    """Load and validate LLM config from path relative to cfgs/."""
    full_path = CFGS_DIR / llm_path
    raw = _load_yaml(full_path)

    adapter = TypeAdapter(LLMConfig)
    config = adapter.validate_python(raw)
    config = validate_and_normalize_reasoning_config(
        config,
        source_path=full_path,
    )
    return _resolve_api_key(config)


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load and validate main config."""
    full_path = CFGS_DIR / config_path
    raw = _load_yaml(full_path)

    # Resolve LLM config paths to actual configs
    if "agents" in raw:
        for agent_name, agent_config in raw["agents"].items():
            if "llm" in agent_config and isinstance(agent_config["llm"], str):
                llm_path = agent_config["llm"]
                try:
                    agent_config["llm"] = resolve_llm_config(llm_path).model_dump()
                except FileNotFoundError:
                    raise SystemExit(
                        f"Config error: agents.{agent_name}.llm "
                        f"references '{llm_path}' which does not exist"
                    )

    return AppConfig.model_validate(raw)
