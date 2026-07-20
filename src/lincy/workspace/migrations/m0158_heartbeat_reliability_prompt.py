"""Refresh brain prompt with heartbeat reliability rules."""

from pathlib import Path
import shutil

from .base import Migration


class M0158HeartbeatReliabilityPrompt(Migration):
    """Deploy prompt rules that keep heartbeat out of durable follow-up duties."""

    version = "0.74.16"
    summary = "Brain prompt: heartbeat reliability and schedule_action follow-up"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
