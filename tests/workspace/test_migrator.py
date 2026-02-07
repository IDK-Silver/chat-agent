"""Tests for Migrator and migration system."""

import pytest
from pathlib import Path

import yaml

from chat_agent.workspace.migrator import Migrator, _parse_version, KERNEL_VERSION
from chat_agent.workspace.migrations.base import Migration


class StubMigration(Migration):
    """Test migration that creates a marker file."""

    version = "0.9.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        (kernel_dir / f"migrated-{self.version}").touch()


class TestParseVersion:
    def test_simple(self):
        assert _parse_version("0.1.3") == (0, 1, 3)

    def test_comparison(self):
        assert _parse_version("0.2.0") > _parse_version("0.1.3")

    def test_comparison_double_digit(self):
        """0.1.10 > 0.1.9 must be correct (string comparison would fail)."""
        assert _parse_version("0.1.10") > _parse_version("0.1.9")


class TestKernelVersion:
    def test_derived_from_migrations(self):
        """KERNEL_VERSION matches the last migration's version."""
        from chat_agent.workspace.migrations import ALL_MIGRATIONS

        assert KERNEL_VERSION == ALL_MIGRATIONS[-1].version


class TestMigrator:
    @pytest.fixture
    def kernel_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "kernel"
        d.mkdir()
        return d

    @pytest.fixture
    def templates_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "templates"
        d.mkdir()
        return d

    def _write_info(self, kernel_dir: Path, version: str) -> None:
        with open(kernel_dir / "info.yaml", "w") as f:
            yaml.dump({"version": version}, f)

    def test_get_current_version(self, kernel_dir, templates_dir):
        self._write_info(kernel_dir, "1.2.3")
        m = Migrator(kernel_dir, templates_dir)
        assert m.get_current_version() == "1.2.3"

    def test_get_current_version_missing(self, kernel_dir, templates_dir):
        (kernel_dir / "info.yaml").unlink(missing_ok=True)
        m = Migrator(kernel_dir, templates_dir)
        assert m.get_current_version() == "0.0.0"

    def test_get_pending_none(self, kernel_dir, templates_dir):
        """No pending when at latest version."""
        self._write_info(kernel_dir, KERNEL_VERSION)
        m = Migrator(kernel_dir, templates_dir)
        assert m.get_pending_migrations() == []

    def test_needs_migration_false(self, kernel_dir, templates_dir):
        self._write_info(kernel_dir, KERNEL_VERSION)
        m = Migrator(kernel_dir, templates_dir)
        assert m.needs_migration() is False

    def test_needs_migration_true(self, kernel_dir, templates_dir):
        self._write_info(kernel_dir, "0.0.1")
        m = Migrator(kernel_dir, templates_dir)
        assert m.needs_migration() is True

    def test_run_migrations(self, kernel_dir, templates_dir):
        """Migrations run and version is updated."""
        self._write_info(kernel_dir, "0.0.1")
        m = Migrator(kernel_dir, templates_dir)
        applied = m.run_migrations()

        assert len(applied) > 0
        assert m.get_current_version() == KERNEL_VERSION
        assert m.needs_migration() is False

    def test_run_migrations_none_pending(self, kernel_dir, templates_dir):
        """No-op when already at latest version."""
        self._write_info(kernel_dir, KERNEL_VERSION)
        m = Migrator(kernel_dir, templates_dir)
        applied = m.run_migrations()
        assert applied == []

    def test_update_version_persists(self, kernel_dir, templates_dir):
        """_update_version writes to info.yaml."""
        self._write_info(kernel_dir, "0.0.1")
        m = Migrator(kernel_dir, templates_dir)
        m._update_version("9.9.9")

        with open(kernel_dir / "info.yaml") as f:
            info = yaml.safe_load(f)
        assert info["version"] == "9.9.9"


class TestM0002AgentsStructure:
    """Tests for the agents/ directory restructure migration."""

    def test_removes_old_system_prompts(self, tmp_path: Path):
        """M0002 removes system-prompts/ directory."""
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        (kernel_dir / "info.yaml").write_text("version: '0.1.3'")
        old_dir = kernel_dir / "system-prompts"
        old_dir.mkdir()
        (old_dir / "brain.md").write_text("old prompt")

        # Use real templates
        from chat_agent.workspace.initializer import WorkspaceInitializer
        from chat_agent.workspace import WorkspaceManager

        manager = WorkspaceManager(tmp_path)
        init = WorkspaceInitializer(manager)
        templates_dir = init._get_templates_dir() / "kernel"

        from chat_agent.workspace.migrations.m0002_agents_structure import M0002AgentsStructure

        m = M0002AgentsStructure()
        m.upgrade(kernel_dir, templates_dir)

        assert not old_dir.exists()

    def test_copies_agents_structure(self, tmp_path: Path):
        """M0002 copies agents/ from templates."""
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        (kernel_dir / "info.yaml").write_text("version: '0.1.3'")

        from chat_agent.workspace.initializer import WorkspaceInitializer
        from chat_agent.workspace import WorkspaceManager

        manager = WorkspaceManager(tmp_path)
        init = WorkspaceInitializer(manager)
        templates_dir = init._get_templates_dir() / "kernel"

        from chat_agent.workspace.migrations.m0002_agents_structure import M0002AgentsStructure

        m = M0002AgentsStructure()
        m.upgrade(kernel_dir, templates_dir)

        assert (kernel_dir / "agents" / "brain" / "prompts" / "system.md").exists()
        assert (kernel_dir / "agents" / "brain" / "prompts" / "shutdown.md").exists()
        assert (kernel_dir / "agents" / "init" / "prompts" / "system.md").exists()

    def test_full_migration_chain(self, tmp_path: Path):
        """Full upgrade from 0.1.3 to latest via migrator."""
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        # Simulate old workspace
        old_dir = kernel_dir / "system-prompts"
        old_dir.mkdir()
        (old_dir / "brain.md").write_text("old")
        (kernel_dir / "info.yaml").write_text("version: '0.1.3'")

        from chat_agent.workspace.initializer import WorkspaceInitializer
        from chat_agent.workspace import WorkspaceManager

        manager = WorkspaceManager(tmp_path)
        init = WorkspaceInitializer(manager)
        templates_dir = init._get_templates_dir() / "kernel"

        m = Migrator(kernel_dir, templates_dir)
        applied = m.run_migrations()

        assert "0.2.0" in applied
        assert m.get_current_version() == KERNEL_VERSION
        assert not old_dir.exists()
        assert (kernel_dir / "agents" / "brain" / "prompts" / "system.md").exists()


class TestM0006ReviewerAgents:
    """Tests for reviewer prompt split migration."""

    def test_moves_existing_reviewer_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        old_prompts = kernel_dir / "agents" / "brain" / "prompts"
        old_prompts.mkdir(parents=True)
        (old_prompts / "reviewer-pre.md").write_text("custom pre reviewer prompt")
        (old_prompts / "reviewer-post.md").write_text("custom post reviewer prompt")

        from chat_agent.workspace.initializer import WorkspaceInitializer
        from chat_agent.workspace import WorkspaceManager
        from chat_agent.workspace.migrations.m0006_reviewer_agents import (
            M0006ReviewerAgents,
        )

        manager = WorkspaceManager(tmp_path)
        templates_dir = WorkspaceInitializer(manager)._get_templates_dir() / "kernel"

        migration = M0006ReviewerAgents()
        migration.upgrade(kernel_dir, templates_dir)

        pre_path = kernel_dir / "agents" / "pre_reviewer" / "prompts" / "system.md"
        post_path = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        assert pre_path.exists()
        assert post_path.exists()
        assert pre_path.read_text() == "custom pre reviewer prompt"
        assert post_path.read_text() == "custom post reviewer prompt"
        assert not (old_prompts / "reviewer-pre.md").exists()
        assert not (old_prompts / "reviewer-post.md").exists()

    def test_copies_template_when_old_prompt_missing(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()

        from chat_agent.workspace.initializer import WorkspaceInitializer
        from chat_agent.workspace import WorkspaceManager
        from chat_agent.workspace.migrations.m0006_reviewer_agents import (
            M0006ReviewerAgents,
        )

        manager = WorkspaceManager(tmp_path)
        templates_dir = WorkspaceInitializer(manager)._get_templates_dir() / "kernel"

        migration = M0006ReviewerAgents()
        migration.upgrade(kernel_dir, templates_dir)

        pre_path = kernel_dir / "agents" / "pre_reviewer" / "prompts" / "system.md"
        post_path = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        assert pre_path.exists()
        assert post_path.exists()
        assert "# Pre-fetch Reviewer" in pre_path.read_text()
        assert "# Post-review Reviewer" in post_path.read_text()


class TestM0007PostReviewerPromptTuning:
    """Tests for post reviewer prompt tuning migration."""

    def test_overwrites_post_reviewer_prompt_from_template(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts"
        dst.mkdir(parents=True)
        (dst / "system.md").write_text("old prompt")

        templates_dir = tmp_path / "templates"
        src = templates_dir / "agents" / "post_reviewer" / "prompts"
        src.mkdir(parents=True)
        (src / "system.md").write_text("new tuned prompt")

        from chat_agent.workspace.migrations.m0007_post_reviewer_prompt_tuning import (
            M0007PostReviewerPromptTuning,
        )

        migration = M0007PostReviewerPromptTuning()
        migration.upgrade(kernel_dir, templates_dir)

        assert (dst / "system.md").read_text() == "new tuned prompt"


class TestM0008PostReviewerStructuredActions:
    """Tests for structured action post-review prompt migration."""

    def test_overwrites_post_reviewer_prompt_from_template(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts"
        dst.mkdir(parents=True)
        (dst / "system.md").write_text("old prompt")

        templates_dir = tmp_path / "templates"
        src = templates_dir / "agents" / "post_reviewer" / "prompts"
        src.mkdir(parents=True)
        (src / "system.md").write_text("new structured actions prompt")

        from chat_agent.workspace.migrations.m0008_post_reviewer_structured_actions import (
            M0008PostReviewerStructuredActions,
        )

        migration = M0008PostReviewerStructuredActions()
        migration.upgrade(kernel_dir, templates_dir)

        assert (dst / "system.md").read_text() == "new structured actions prompt"


class TestM0009ShutdownReviewerPrompt:
    """Tests for shutdown reviewer prompt migration."""

    def test_copies_shutdown_reviewer_prompt_from_template(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"
        src = templates_dir / "agents" / "shutdown_reviewer" / "prompts"
        dst = kernel_dir / "agents" / "shutdown_reviewer" / "prompts"
        src.mkdir(parents=True)
        (src / "system.md").write_text("shutdown reviewer prompt")

        from chat_agent.workspace.migrations.m0009_shutdown_reviewer_prompt import (
            M0009ShutdownReviewerPrompt,
        )

        migration = M0009ShutdownReviewerPrompt()
        migration.upgrade(kernel_dir, templates_dir)

        assert (dst / "system.md").read_text() == "shutdown reviewer prompt"
