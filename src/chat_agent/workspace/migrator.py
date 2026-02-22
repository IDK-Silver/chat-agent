"""Kernel migration runner."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .migrations import ALL_MIGRATIONS
from .migrations.base import Migration


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse semver string to comparable tuple."""
    return tuple(int(x) for x in v.split("."))


# Derived from the last registered migration
KERNEL_VERSION = ALL_MIGRATIONS[-1].version


@dataclass
class MigrationResult:
    """Result of running migrations."""

    applied_versions: list[str] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    old_version: str = ""
    new_version: str = ""

    @property
    def upgraded(self) -> bool:
        return len(self.applied_versions) > 0

    def format_startup_message(self) -> str:
        """Format upgrade info for injection into STARTUP message."""
        if not self.upgraded:
            return ""
        lines = [
            f"[STARTUP after upgrade]",
            f"version: {self.old_version} -> {self.new_version}",
        ]
        if self.summaries:
            lines.append("changes:")
            for s in self.summaries:
                lines.append(f"- {s}")
        return "\n".join(lines)


class Migrator:
    """Runs kernel migrations sequentially."""

    def __init__(self, kernel_dir: Path, templates_dir: Path):
        self.kernel_dir = kernel_dir
        self.templates_dir = templates_dir

    def get_current_version(self) -> str:
        """Read version from kernel/info.yaml."""
        info_path = self.kernel_dir / "info.yaml"
        if not info_path.exists():
            return "0.0.0"

        with open(info_path) as f:
            info = yaml.safe_load(f)
        return info.get("version", "0.0.0")

    def get_pending_migrations(self) -> list[Migration]:
        """Return migrations newer than current version."""
        current = _parse_version(self.get_current_version())
        return [m for m in ALL_MIGRATIONS if _parse_version(m.version) > current]

    def needs_migration(self) -> bool:
        """Check if any migrations are pending."""
        return len(self.get_pending_migrations()) > 0

    def run_migrations(self) -> MigrationResult:
        """Run all pending migrations in order.

        Returns:
            MigrationResult with applied versions and summaries.
        """
        old_version = self.get_current_version()
        pending = self.get_pending_migrations()
        result = MigrationResult(old_version=old_version)

        for migration in pending:
            migration.upgrade(self.kernel_dir, self.templates_dir)
            self._update_version(migration.version)
            result.applied_versions.append(migration.version)
            if migration.summary:
                result.summaries.append(migration.summary)

        if result.applied_versions:
            result.new_version = result.applied_versions[-1]

        return result

    def _update_version(self, version: str) -> None:
        """Update version in kernel/info.yaml."""
        info_path = self.kernel_dir / "info.yaml"
        if info_path.exists():
            with open(info_path) as f:
                info = yaml.safe_load(f) or {}
        else:
            info = {}

        info["version"] = version
        with open(info_path, "w") as f:
            yaml.dump(info, f, default_flow_style=False)
