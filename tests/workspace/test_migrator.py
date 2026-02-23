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
        result = m.run_migrations()

        assert result.upgraded
        assert len(result.applied_versions) > 0
        assert m.get_current_version() == KERNEL_VERSION
        assert m.needs_migration() is False

    def test_run_migrations_none_pending(self, kernel_dir, templates_dir):
        """No-op when already at latest version."""
        self._write_info(kernel_dir, KERNEL_VERSION)
        m = Migrator(kernel_dir, templates_dir)
        result = m.run_migrations()
        assert not result.upgraded

    def test_update_version_persists(self, kernel_dir, templates_dir):
        """_update_version writes to info.yaml."""
        self._write_info(kernel_dir, "0.0.1")
        m = Migrator(kernel_dir, templates_dir)
        m._update_version("9.9.9")

        with open(kernel_dir / "info.yaml") as f:
            info = yaml.safe_load(f)
        assert info["version"] == "9.9.9"

    def test_run_migrations_removes_timezone_from_info_yaml(self, kernel_dir, templates_dir):
        with open(kernel_dir / "info.yaml", "w") as f:
            yaml.dump(
                {
                    # Start at pre-m0090 version so only the timezone-removal
                    # migration is pending in this unit test.
                    "version": "0.54.0",
                    "updated": "2026-02-21",
                    "timezone": "Asia/Taipei",
                    "custom": "keep-me",
                },
                f,
            )

        m = Migrator(kernel_dir, templates_dir)
        result = m.run_migrations()

        assert result.upgraded
        with open(kernel_dir / "info.yaml") as f:
            info = yaml.safe_load(f)
        assert info["version"] == KERNEL_VERSION
        assert info["custom"] == "keep-me"
        assert "timezone" not in info


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
        result = m.run_migrations()

        assert "0.2.0" in result.applied_versions
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

        # Reviewer templates have been removed; migration skips gracefully
        post_path = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        assert not post_path.exists()


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


class TestM0030StrictTargetAnomalySignals:
    """Tests for strict target/anomaly prompt migration."""

    def test_copies_brain_and_post_reviewer_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"
        (kernel_dir / "agents" / "brain" / "prompts").mkdir(parents=True)
        (kernel_dir / "agents" / "post_reviewer" / "prompts").mkdir(parents=True)
        (kernel_dir / "agents" / "shutdown_reviewer" / "prompts").mkdir(parents=True)
        (templates_dir / "agents" / "brain" / "prompts").mkdir(parents=True)
        (templates_dir / "agents" / "post_reviewer" / "prompts").mkdir(parents=True)
        (templates_dir / "agents" / "shutdown_reviewer" / "prompts").mkdir(parents=True)

        (templates_dir / "agents" / "brain" / "prompts" / "system.md").write_text(
            "brain strict v0.9.0"
        )
        (templates_dir / "agents" / "brain" / "prompts" / "shutdown.md").write_text(
            "brain shutdown strict v0.9.0"
        )
        (templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md").write_text(
            "post reviewer strict v0.9.0"
        )
        (templates_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md").write_text(
            "parse retry strict v0.9.0"
        )
        (templates_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md").write_text(
            "shutdown reviewer strict v0.9.0"
        )
        (templates_dir / "agents" / "shutdown_reviewer" / "prompts" / "parse-retry.md").write_text(
            "shutdown parse retry strict v0.9.0"
        )

        from chat_agent.workspace.migrations.m0030_strict_target_anomaly_signals import (
            M0030StrictTargetAnomalySignals,
        )

        migration = M0030StrictTargetAnomalySignals()
        migration.upgrade(kernel_dir, templates_dir)

        assert (kernel_dir / "agents" / "brain" / "prompts" / "system.md").read_text() == (
            "brain strict v0.9.0"
        )
        assert (
            kernel_dir / "agents" / "brain" / "prompts" / "shutdown.md"
        ).read_text() == "brain shutdown strict v0.9.0"
        assert (
            kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        ).read_text() == "post reviewer strict v0.9.0"
        assert (
            kernel_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md"
        ).read_text() == "parse retry strict v0.9.0"
        assert (
            kernel_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md"
        ).read_text() == "shutdown reviewer strict v0.9.0"
        assert (
            kernel_dir / "agents" / "shutdown_reviewer" / "prompts" / "parse-retry.md"
        ).read_text() == "shutdown parse retry strict v0.9.0"


class TestM0031MemorySearchTwoStageConfigurableLimits:
    """Tests for memory_searcher prompt refresh migration."""

    def test_copies_memory_searcher_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"
        (kernel_dir / "agents" / "memory_searcher" / "prompts").mkdir(parents=True)
        (templates_dir / "agents" / "memory_searcher" / "prompts").mkdir(parents=True)

        (templates_dir / "agents" / "memory_searcher" / "prompts" / "system.md").write_text(
            "memory searcher two-stage v0.9.1"
        )
        (templates_dir / "agents" / "memory_searcher" / "prompts" / "parse-retry.md").write_text(
            "memory searcher parse retry v0.9.1"
        )

        from chat_agent.workspace.migrations.m0031_memory_search_two_stage_configurable_limits import (
            M0031MemorySearchTwoStageConfigurableLimits,
        )

        migration = M0031MemorySearchTwoStageConfigurableLimits()
        migration.upgrade(kernel_dir, templates_dir)

        assert (
            kernel_dir / "agents" / "memory_searcher" / "prompts" / "system.md"
        ).read_text() == "memory searcher two-stage v0.9.1"
        assert (
            kernel_dir / "agents" / "memory_searcher" / "prompts" / "parse-retry.md"
        ).read_text() == "memory searcher parse retry v0.9.1"


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


class TestM0010ReviewerParseRetryPrompts:
    """Tests for reviewer parse-retry prompt migration."""

    def test_copies_parse_retry_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        pre_src = templates_dir / "agents" / "pre_reviewer" / "prompts"
        post_src = templates_dir / "agents" / "post_reviewer" / "prompts"
        shutdown_src = templates_dir / "agents" / "shutdown_reviewer" / "prompts"
        pre_dst = kernel_dir / "agents" / "pre_reviewer" / "prompts"
        post_dst = kernel_dir / "agents" / "post_reviewer" / "prompts"
        shutdown_dst = kernel_dir / "agents" / "shutdown_reviewer" / "prompts"

        pre_src.mkdir(parents=True)
        post_src.mkdir(parents=True)
        shutdown_src.mkdir(parents=True)
        (pre_src / "parse-retry.md").write_text("pre parse retry prompt")
        (post_src / "parse-retry.md").write_text("post parse retry prompt")
        (shutdown_src / "parse-retry.md").write_text("shutdown parse retry prompt")

        from chat_agent.workspace.migrations.m0010_reviewer_parse_retry_prompts import (
            M0010ReviewerParseRetryPrompts,
        )

        migration = M0010ReviewerParseRetryPrompts()
        migration.upgrade(kernel_dir, templates_dir)

        assert (pre_dst / "parse-retry.md").read_text() == "pre parse retry prompt"
        assert (post_dst / "parse-retry.md").read_text() == "post parse retry prompt"
        assert (shutdown_dst / "parse-retry.md").read_text() == "shutdown parse retry prompt"


class TestM0011SystemPromptFormatting:
    """Tests for system prompt formatting migration."""

    def test_copies_system_prompt(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        src = templates_dir / "agents" / "brain" / "prompts"
        dst = kernel_dir / "agents" / "brain" / "prompts"

        src.mkdir(parents=True)
        dst.mkdir(parents=True)
        (dst / "system.md").write_text("old prompt")
        (src / "system.md").write_text("new prompt with formatting")

        from chat_agent.workspace.migrations.m0011_system_prompt_formatting import (
            M0011SystemPromptFormatting,
        )

        migration = M0011SystemPromptFormatting()
        migration.upgrade(kernel_dir, templates_dir)

        assert (dst / "system.md").read_text() == "new prompt with formatting"


class TestM0012TurnPersistencePromptTuning:
    """Tests for turn persistence prompt tuning migration."""

    def test_copies_brain_and_post_reviewer_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        brain_src = templates_dir / "agents" / "brain" / "prompts"
        post_src = templates_dir / "agents" / "post_reviewer" / "prompts"
        brain_dst = kernel_dir / "agents" / "brain" / "prompts"
        post_dst = kernel_dir / "agents" / "post_reviewer" / "prompts"

        brain_src.mkdir(parents=True)
        post_src.mkdir(parents=True)
        brain_dst.mkdir(parents=True)
        post_dst.mkdir(parents=True)

        (brain_dst / "system.md").write_text("old brain prompt")
        (post_dst / "system.md").write_text("old post prompt")
        (brain_src / "system.md").write_text("new brain prompt")
        (post_src / "system.md").write_text("new post prompt")

        from chat_agent.workspace.migrations.m0012_turn_persistence_prompt_tuning import (
            M0012TurnPersistencePromptTuning,
        )

        migration = M0012TurnPersistencePromptTuning()
        migration.upgrade(kernel_dir, templates_dir)

        assert (brain_dst / "system.md").read_text() == "new brain prompt"
        assert (post_dst / "system.md").read_text() == "new post prompt"


class TestM0013MemoryWriterPipeline:
    """Tests for memory writer pipeline prompt migration."""

    def test_copies_memory_writer_and_related_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        mappings = [
            ("agents/brain/prompts/system.md", "brain system"),
            ("agents/brain/prompts/shutdown.md", "brain shutdown"),
            ("agents/post_reviewer/prompts/system.md", "post reviewer"),
            ("agents/shutdown_reviewer/prompts/system.md", "shutdown reviewer"),
            ("agents/memory_writer/prompts/system.md", "memory writer system"),
            ("agents/memory_writer/prompts/parse-retry.md", "memory writer parse retry"),
        ]

        for relative_path, content in mappings:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content)
            dst.write_text("old")

        from chat_agent.workspace.migrations.m0013_memory_writer_pipeline import (
            M0013MemoryWriterPipeline,
        )

        migration = M0013MemoryWriterPipeline()
        migration.upgrade(kernel_dir, templates_dir)

        for relative_path, content in mappings:
            assert (kernel_dir / relative_path).read_text() == content


class TestM0014RecentContextPriority:
    """Tests for recent-context priority prompt migration."""

    def test_copies_recent_context_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        mappings = [
            ("agents/brain/prompts/system.md", "brain recent-context prompt"),
            ("agents/pre_reviewer/prompts/system.md", "pre reviewer recent-context prompt"),
            ("agents/post_reviewer/prompts/system.md", "post reviewer recent-context prompt"),
        ]

        for relative_path, content in mappings:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content)
            dst.write_text("old")

        from chat_agent.workspace.migrations.m0014_recent_context_priority import (
            M0014RecentContextPriority,
        )

        migration = M0014RecentContextPriority()
        migration.upgrade(kernel_dir, templates_dir)

        for relative_path, content in mappings:
            assert (kernel_dir / relative_path).read_text() == content


class TestM0066ProgressReviewer:
    """Tests for progress reviewer prompt migration."""

    def test_copies_progress_reviewer_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        mappings = [
            ("agents/progress_reviewer/prompts/system.md", "progress reviewer system"),
            ("agents/progress_reviewer/prompts/parse-retry.md", "progress reviewer parse retry"),
        ]

        for relative_path, content in mappings:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content)
            dst.write_text("old")

        from chat_agent.workspace.migrations.m0066_progress_reviewer import (
            M0066ProgressReviewer,
        )

        migration = M0066ProgressReviewer()
        migration.upgrade(kernel_dir, templates_dir)

        for relative_path, content in mappings:
            assert (kernel_dir / relative_path).read_text() == content


class TestM0067CompletionReviewerPrompts:
    """Tests for completion-gate reviewer prompt migration."""

    def test_copies_completion_reviewer_prompts(self, tmp_path: Path):
        kernel_dir = tmp_path / "kernel"
        templates_dir = tmp_path / "templates"

        mappings = [
            ("agents/post_reviewer/prompts/system.md", "post reviewer completion system"),
            ("agents/post_reviewer/prompts/parse-retry.md", "post reviewer completion parse"),
            ("agents/shutdown_reviewer/prompts/system.md", "shutdown reviewer completion system"),
            ("agents/shutdown_reviewer/prompts/parse-retry.md", "shutdown reviewer completion parse"),
            ("agents/progress_reviewer/prompts/system.md", "progress reviewer advisory system"),
            ("agents/progress_reviewer/prompts/parse-retry.md", "progress reviewer parse retry"),
        ]

        for relative_path, content in mappings:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(content)
            dst.write_text("old")

        from chat_agent.workspace.migrations.m0067_completion_reviewer_prompts import (
            M0067CompletionReviewerPrompts,
        )

        migration = M0067CompletionReviewerPrompts()
        migration.upgrade(kernel_dir, templates_dir)

        for relative_path, content in mappings:
            assert (kernel_dir / relative_path).read_text() == content
