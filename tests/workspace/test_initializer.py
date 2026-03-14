"""Tests for WorkspaceInitializer."""

from pathlib import Path

from chat_agent.workspace import (
    WorkspaceManager,
    WorkspaceInitializer,
    KERNEL_VERSION,
)


class TestWorkspaceInitializer:
    def test_create_structure(self, tmp_path: Path):
        """create_structure creates complete workspace."""
        agent_os_dir = tmp_path / "workspace"
        manager = WorkspaceManager(agent_os_dir)
        initializer = WorkspaceInitializer(manager)

        initializer.create_structure()

        # Check kernel
        assert (agent_os_dir / "kernel" / "info.yaml").exists()
        info_text = (agent_os_dir / "kernel" / "info.yaml").read_text()
        assert "timezone:" not in info_text
        assert (agent_os_dir / "kernel" / "agents" / "brain" / "prompts" / "system.md").exists()
        assert (agent_os_dir / "kernel" / "agents" / "init" / "prompts" / "system.md").exists()
        assert (agent_os_dir / "kernel" / "builtin-skills" / "index.md").exists()
        assert (agent_os_dir / "kernel" / "builtin-skills" / "discord-messaging" / "guide.md").exists()
        assert (agent_os_dir / "kernel" / "builtin-skills" / "discord-messaging" / "meta.yaml").exists()
        brain_prompt = (
            agent_os_dir / "kernel" / "agents" / "brain" / "prompts" / "system.md"
        ).read_text()
        assert "### `execute_shell` 使用指引" in brain_prompt
        assert "execute_shell` 只適用於**非互動式** shell 指令" in brain_prompt
        assert "### `web_search` 使用指引" in brain_prompt
        assert "當問題涉及**最新、今天、目前" in brain_prompt
        assert "### `shell_task` 使用指引" in brain_prompt
        assert "`shell_task` 適用於背景 shell 工作" in brain_prompt
        assert "OAuth/login/device-code" in brain_prompt

        # Check memory
        assert (agent_os_dir / "memory" / "agent" / "index.md").exists()
        assert (agent_os_dir / "memory" / "agent" / "persona.md").exists()
        assert (agent_os_dir / "memory" / "agent" / "temp-memory.md").exists()
        assert (agent_os_dir / "memory" / "agent" / "artifacts.md").exists()
        assert (agent_os_dir / "memory" / "agent" / "identity" / "index.md").exists()
        assert (agent_os_dir / "memory" / "archive" / "index.md").exists()
        assert (agent_os_dir / "memory" / "archive" / "deprecated" / "index.md").exists()
        assert (agent_os_dir / "memory" / "archive" / "temp-memory" / "index.md").exists()
        assert (agent_os_dir / "memory" / "people" / "index.md").exists()
        assert (agent_os_dir / "artifacts" / "files").is_dir()
        assert (agent_os_dir / "artifacts" / "creations").is_dir()
        assert (agent_os_dir / "state").is_dir()

    def test_create_structure_idempotent(self, tmp_path: Path):
        """create_structure does nothing if already initialized."""
        manager = WorkspaceManager(tmp_path)

        # Manually create info.yaml
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text("version: '0.1.0'")

        initializer = WorkspaceInitializer(manager)
        initializer.create_structure()  # Should not raise or overwrite

        # Memory should not be created
        assert not (tmp_path / "memory").exists()

    def test_needs_upgrade_not_initialized(self, tmp_path: Path):
        """needs_upgrade returns True for uninitialized workspace."""
        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is True

    def test_needs_upgrade_same_version(self, tmp_path: Path):
        """needs_upgrade returns False for current version."""
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text(f"version: '{KERNEL_VERSION}'")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is False

    def test_needs_upgrade_old_version(self, tmp_path: Path):
        """needs_upgrade returns True for old version."""
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text("version: '0.0.1'")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is True

    def test_upgrade_kernel_preserves_memory(self, tmp_path: Path):
        """upgrade_kernel replaces kernel but keeps memory."""
        # Setup initial state
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        (kernel_dir / "info.yaml").write_text("version: '0.0.1'")
        (kernel_dir / "old_file.txt").write_text("old")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "user_data.md").write_text("precious data")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        result = initializer.upgrade_kernel()

        # Returns MigrationResult with applied versions
        assert result.upgraded
        assert len(result.applied_versions) > 0

        # Memory preserved
        assert (memory_dir / "user_data.md").read_text() == "precious data"

        # Version updated
        assert manager.get_kernel_version() == KERNEL_VERSION
        brain_prompt = (kernel_dir / "agents" / "brain" / "prompts" / "system.md").read_text()
        assert "### `execute_shell` 使用指引" in brain_prompt
        assert "需要用戶在別台裝置完成 OAuth link、2FA、或授權確認時" in brain_prompt
        assert "### `web_search` 使用指引" in brain_prompt
        assert "第三方產品行為" in brain_prompt
        assert "### `shell_task` 使用指引" in brain_prompt
        assert "結果一定是**下一輪**才會收到" in brain_prompt
        assert "系統會直接提示使用者" in brain_prompt

    def test_upgrade_kernel_creates_backup(self, tmp_path: Path):
        """upgrade_kernel creates a backup before applying migrations."""
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        (kernel_dir / "info.yaml").write_text("version: '0.0.1'")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "user_data.md").write_text("precious data")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        initializer.upgrade_kernel()

        # Backup directory exists with one backup
        backups_dir = tmp_path / "backups"
        assert backups_dir.exists()
        backups = list(backups_dir.iterdir())
        assert len(backups) == 1

        # Backup contains pre-upgrade state
        backup = backups[0]
        assert backup.name.startswith("v0.0.1_")
        assert (backup / "kernel" / "info.yaml").exists()
        assert (backup / "memory" / "user_data.md").read_text() == "precious data"

    def test_upgrade_kernel_prunes_numbered_prompt_duplicates(self, tmp_path: Path):
        """upgrade_kernel removes Finder/iCloud conflict copies for managed prompts."""
        kernel_dir = tmp_path / "kernel"
        prompt_dir = kernel_dir / "agents" / "brain" / "prompts"
        prompt_dir.mkdir(parents=True)
        (kernel_dir / "info.yaml").write_text("version: '0.0.1'")
        (prompt_dir / "system.md").write_text("old prompt")
        (prompt_dir / "system 2.md").write_text("duplicate")
        (prompt_dir / ".DS_Store").write_text("metadata")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        initializer.upgrade_kernel()

        assert (prompt_dir / "system.md").exists()
        assert not (prompt_dir / "system 2.md").exists()
        assert not (prompt_dir / ".DS_Store").exists()

    def test_prune_prompt_dir_duplicates_keeps_non_managed_filenames(self, tmp_path: Path):
        """Only numbered copies of the managed filename should be removed."""
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "system.md").write_text("canonical")
        (prompt_dir / "system 2.md").write_text("duplicate")
        (prompt_dir / "system note.md").write_text("keep")
        (prompt_dir / "describe 2.md").write_text("keep")

        WorkspaceInitializer._prune_prompt_dir_duplicates(prompt_dir, "system.md")

        assert (prompt_dir / "system.md").exists()
        assert not (prompt_dir / "system 2.md").exists()
        assert (prompt_dir / "system note.md").exists()
        assert (prompt_dir / "describe 2.md").exists()
