"""Base class for kernel migrations."""

from abc import ABC, abstractmethod
from pathlib import Path


class Migration(ABC):
    """A single version upgrade step for kernel."""

    version: str  # Target version after this migration
    summary: str = ""  # Agent-facing upgrade summary (Traditional Chinese)

    @abstractmethod
    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        """Execute the migration.

        Args:
            kernel_dir: Live workspace kernel directory.
            templates_dir: Package templates/kernel directory.
        """

    @property
    def name(self) -> str:
        return self.__class__.__name__
