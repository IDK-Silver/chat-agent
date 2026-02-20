"""Load and validate supervisor.yaml."""

from pathlib import Path

import yaml

from .schema import SupervisorConfig

CFGS_DIR = Path(__file__).parent.parent.parent / "cfgs"


def load_supervisor_config(
    config_path: str = "supervisor.yaml",
) -> SupervisorConfig:
    """Load and validate supervisor config from cfgs/ directory."""
    full_path = CFGS_DIR / config_path
    with open(full_path) as f:
        raw = yaml.safe_load(f)
    return SupervisorConfig.model_validate(raw or {})
