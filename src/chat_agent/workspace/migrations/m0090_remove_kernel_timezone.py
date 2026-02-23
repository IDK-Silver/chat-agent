"""Remove deprecated timezone field from kernel/info.yaml."""

from pathlib import Path

import yaml

from .base import Migration


class M0090RemoveKernelTimezone(Migration):
    """Move runtime timezone config ownership to cfgs/agent.yaml."""

    version = "0.55.0"
    summary = "移除 kernel/info.yaml 的 timezone，改由 agent.yaml 管理"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        del templates_dir  # Not needed for this migration.

        info_path = kernel_dir / "info.yaml"
        if not info_path.exists():
            return

        with open(info_path) as f:
            info = yaml.safe_load(f) or {}

        if "timezone" not in info:
            return

        info.pop("timezone", None)
        with open(info_path, "w") as f:
            yaml.dump(info, f, default_flow_style=False)
