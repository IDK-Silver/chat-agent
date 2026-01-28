import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

CFGS_DIR = Path(__file__).parent.parent.parent.parent / "cfgs"


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_llm_config(llm_path: str) -> dict:
    """Load LLM config from path relative to cfgs/."""
    full_path = CFGS_DIR / llm_path
    config = load_yaml(full_path)

    # Resolve API key from environment variable if specified
    if "api_key_env" in config:
        env_var = config["api_key_env"]
        config["api_key"] = os.getenv(env_var)
        del config["api_key_env"]

    return config


def load_config(config_path: str = "basic.yaml") -> dict:
    """Load main config and resolve all path references."""
    full_path = CFGS_DIR / config_path
    config = load_yaml(full_path)

    # Resolve LLM configs for each agent
    if "agents" in config:
        for agent_name, agent_config in config["agents"].items():
            if "llm" in agent_config:
                agent_config["llm"] = resolve_llm_config(agent_config["llm"])

    return config
