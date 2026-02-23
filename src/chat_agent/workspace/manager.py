"""Workspace management utilities."""

from datetime import date
from pathlib import Path

import yaml


class WorkspaceManager:
    """Manages the workspace directory (kernel + memory).

    The workspace contains:
    - kernel/ - Upgradable system core (system prompts, version info)
    - memory/ - User data (preserved during upgrades)
    """

    def __init__(self, agent_os_dir: Path):
        self.agent_os_dir = agent_os_dir
        self.kernel_dir = agent_os_dir / "kernel"
        self.memory_dir = agent_os_dir / "memory"
        self.system_prompts_dir = self.kernel_dir / "system-prompts"

    @property
    def agents_dir(self) -> Path:
        """Path to agents directory within kernel."""
        return self.kernel_dir / "agents"

    def is_initialized(self) -> bool:
        """Check if workspace is initialized (kernel/info.yaml exists)."""
        return (self.kernel_dir / "info.yaml").exists()

    def get_kernel_version(self) -> str:
        """Read version from kernel/info.yaml."""
        info_path = self.kernel_dir / "info.yaml"
        if not info_path.exists():
            raise FileNotFoundError("Workspace not initialized")

        with open(info_path) as f:
            info = yaml.safe_load(f)
        return info.get("version", "unknown")

    def get_agent_prompt(
        self,
        agent_name: str,
        prompt_name: str,
        current_user: str | None = None,
    ) -> str:
        """Load prompt from agents/{agent}/prompts/{prompt}.md.

        Supports placeholders: {agent_os_dir}, {current_user}, {date}.
        """
        prompt_path = self.agents_dir / agent_name / "prompts" / f"{prompt_name}.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt not found: {agent_name}/{prompt_name}")

        content = prompt_path.read_text()
        return self._resolve_placeholders(content, current_user)

    def get_system_prompt(self, agent_name: str) -> str:
        """Load raw system prompt for specified agent.

        System prompts are static templates; runtime values are injected
        separately via ContextBuilder.
        """
        new_path = self.agents_dir / agent_name / "prompts" / "system.md"
        if new_path.exists():
            return new_path.read_text()

        # Legacy fallback
        prompt_path = self.system_prompts_dir / f"{agent_name}.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"System prompt not found: {agent_name}")
        return prompt_path.read_text()

    def _resolve_placeholders(self, content: str, current_user: str | None = None) -> str:
        """Replace placeholders in prompt content."""
        content = content.replace("{agent_os_dir}", str(self.agent_os_dir))

        if "{date}" in content:
            content = content.replace("{date}", date.today().isoformat())

        if "{current_user}" in content:
            if current_user is None:
                raise ValueError("current_user is required for this prompt")
            content = content.replace("{current_user}", current_user)

        return content

    def resolve_memory_path(self, relative_path: str) -> Path:
        """Resolve path within memory directory, ensure it stays within bounds.

        Args:
            relative_path: Path relative to memory/ directory

        Returns:
            Resolved absolute path

        Raises:
            ValueError: If path escapes memory directory
        """
        target = (self.memory_dir / relative_path).resolve()

        # Security check: ensure path is within memory_dir
        try:
            target.relative_to(self.memory_dir.resolve())
        except ValueError:
            raise ValueError(f"Path escapes memory directory: {relative_path}")

        return target
