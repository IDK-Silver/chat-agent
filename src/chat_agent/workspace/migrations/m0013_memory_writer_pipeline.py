"""Migration to enable memory writer pipeline prompts."""

import shutil
from pathlib import Path

from .base import Migration


class M0013MemoryWriterPipeline(Migration):
    """Copy prompts required by memory_edit + memory_writer pipeline."""

    version = "0.5.7"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "brain" / "prompts" / "system.md",
                kernel_dir / "agents" / "brain" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "brain" / "prompts" / "shutdown.md",
                kernel_dir / "agents" / "brain" / "prompts" / "shutdown.md",
            ),
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "memory_writer" / "prompts" / "system.md",
                kernel_dir / "agents" / "memory_writer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "memory_writer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "memory_writer" / "prompts" / "parse-retry.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
