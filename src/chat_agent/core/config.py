import os
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit

import yaml
from dotenv import dotenv_values
from pydantic import TypeAdapter

from .schema import (
    AnthropicConfig,
    AppConfig,
    CopilotConfig,
    GeminiConfig,
    LLMConfig,
    OllamaConfig,
    OpenAIConfig,
    OpenRouterConfig,
)

_dotenv_values = dotenv_values()

CFGS_DIR = Path(__file__).parent.parent.parent.parent / "cfgs"

T = TypeVar(
    "T",
    OllamaConfig,
    CopilotConfig,
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

    api_key = _dotenv_values.get(config.api_key_env) or os.getenv(config.api_key_env)
    return config.model_copy(update={"api_key": api_key, "api_key_env": None})


def _derive_agent_site_url(base_url: str, agent_name: str) -> str:
    """Append agent path for per-agent OpenRouter attribution."""
    base = base_url.strip()
    if not base:
        return agent_name

    parts = urlsplit(base)
    if parts.scheme and parts.netloc:
        path = parts.path.rstrip("/")
        new_path = f"{path}/{agent_name}" if path else f"/{agent_name}"
        return urlunsplit(
            (parts.scheme, parts.netloc, new_path, parts.query, parts.fragment)
        )

    trimmed = base.rstrip("/")
    if not trimmed:
        return agent_name
    return f"{trimmed}/{agent_name}"


def resolve_llm_config(llm_path: str) -> LLMConfig:
    """Load and validate LLM config from path relative to cfgs/."""
    full_path = CFGS_DIR / llm_path
    raw = _load_yaml(full_path)

    adapter = TypeAdapter(LLMConfig)
    config = adapter.validate_python(raw)
    config = config.validate_reasoning(source_path=full_path)
    return _resolve_api_key(config)


def load_config(config_path: str = "agent.yaml") -> AppConfig:
    """Load and validate main config."""
    full_path = CFGS_DIR / config_path
    raw = _load_yaml(full_path)

    # Resolve LLM config paths to actual configs
    if "agents" in raw:
        for agent_name, agent_config in raw["agents"].items():
            if "llm" in agent_config and isinstance(agent_config["llm"], str):
                llm_path = agent_config["llm"]
                try:
                    config = resolve_llm_config(llm_path)
                except FileNotFoundError:
                    raise SystemExit(
                        f"Config error: agents.{agent_name}.llm "
                        f"references '{llm_path}' which does not exist"
                    )
                if isinstance(config, OpenRouterConfig):
                    # Apply per-agent OpenRouter attribution fields.
                    agent_or = agent_config.get("openrouter")
                    agent_or_dict = agent_or if isinstance(agent_or, dict) else {}

                    site_name = config.site_name
                    if site_name is None:
                        site_name = (
                            agent_or_dict["site_name"]
                            if agent_or_dict.get("site_name")
                            else agent_name
                        )

                    site_url = config.site_url
                    if (
                        "site_url" in agent_or_dict
                        and agent_or_dict["site_url"] is not None
                    ):
                        site_url = agent_or_dict["site_url"]
                    elif site_url is not None:
                        site_url = _derive_agent_site_url(site_url, agent_name)

                    config = config.model_copy(
                        update={"site_name": site_name, "site_url": site_url}
                    )
                agent_config["llm"] = config.model_dump()

    return AppConfig.model_validate(raw)
