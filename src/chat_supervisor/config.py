"""Load and validate supervisor.yaml."""

from copy import deepcopy
from pathlib import Path

import yaml

from chat_agent.core.config import load_config as load_agent_config

from .schema import SupervisorConfig

CFGS_DIR = Path(__file__).parent.parent.parent / "cfgs"


def _used_agent_llm_providers(agent_config_path: str = "agent.yaml") -> set[str]:
    """Return enabled agent providers from cfgs/agent.yaml."""

    config = load_agent_config(agent_config_path)
    return {
        agent.llm.provider
        for agent in config.agents.values()
        if agent.enabled
    }


def _resolve_auto_enabled_processes(raw: dict) -> dict:
    """Expand `enabled: auto` into concrete booleans before schema validation."""

    resolved = deepcopy(raw or {})
    processes = resolved.get("processes")
    if not isinstance(processes, dict):
        return resolved

    used_providers: set[str] | None = None
    for name, process in processes.items():
        if not isinstance(process, dict) or process.get("enabled") != "auto":
            continue
        provider = process.get("auto_enable_when_any_agent_uses_provider")
        if not isinstance(provider, str) or not provider:
            raise ValueError(
                f"processes.{name}.enabled=auto requires "
                "auto_enable_when_any_agent_uses_provider"
            )
        if used_providers is None:
            used_providers = _used_agent_llm_providers()
        process["enabled"] = provider in used_providers

    return resolved


def load_supervisor_config(
    config_path: str = "supervisor.yaml",
) -> SupervisorConfig:
    """Load and validate supervisor config from cfgs/ directory."""
    full_path = CFGS_DIR / config_path
    with open(full_path) as f:
        raw = yaml.safe_load(f)
    return SupervisorConfig.model_validate(_resolve_auto_enabled_processes(raw or {}))
