"""Refresh brain prompt with self-improvement and anti-sycophancy rules."""

from pathlib import Path
import shutil

from .base import Migration


class M0153SelfImprovementPrompt(Migration):
    """Deploy brain prompt guidance for persona/long-term/skills improvement."""

    version = "0.74.11"
    summary = "Brain prompt: 主動改進 persona、long-term、skills，同時避免討好型人格"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
