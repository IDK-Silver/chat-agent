"""Workspace management utilities."""

from pathlib import Path

import yaml


class WorkspaceManager:
    """Manages the workspace directory (kernel + memory).

    The workspace contains:
    - kernel/ - Upgradable system core (system prompts, version info)
    - memory/ - User data (preserved during upgrades)
    """

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.kernel_dir = working_dir / "kernel"
        self.memory_dir = working_dir / "memory"
        self.system_prompts_dir = self.kernel_dir / "system-prompts"

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

    def get_timezone(self) -> str:
        """Read timezone from kernel/info.yaml, default to Asia/Taipei."""
        info_path = self.kernel_dir / "info.yaml"
        if not info_path.exists():
            return "Asia/Taipei"
        with open(info_path) as f:
            info = yaml.safe_load(f)
        return info.get("timezone", "Asia/Taipei")

    def get_system_prompt(self, agent_name: str, current_user: str | None = None) -> str:
        """Load system prompt for specified agent.

        Args:
            agent_name: Name of the agent (e.g., "brain", "init")
            current_user: Current user_id for the session (optional)

        Returns:
            The system prompt content with working_dir path injected.
        """
        prompt_path = self.system_prompts_dir / f"{agent_name}.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"System prompt not found: {agent_name}")

        content = prompt_path.read_text()
        content = content.replace("{working_dir}", str(self.working_dir))

        if "{current_user}" in content:
            if current_user is None:
                raise ValueError("current_user is required for this system prompt")
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
