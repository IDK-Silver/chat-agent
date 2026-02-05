"""Tests for shell command executor."""

from pathlib import Path

import pytest

from chat_agent.tools.executor import ShellExecutor


class TestShellExecutor:
    def test_basic_command(self, tmp_path: Path):
        """Basic command execution works."""
        executor = ShellExecutor(working_dir=tmp_path)
        result = executor.execute("echo hello")
        assert "hello" in result

    def test_cwd_tracking(self, tmp_path: Path):
        """Working directory is tracked across commands."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        executor = ShellExecutor(working_dir=tmp_path)
        assert executor.cwd == tmp_path

        executor.execute(f"cd {subdir}")
        assert executor.cwd == subdir

    def test_cwd_persists(self, tmp_path: Path):
        """Commands run in tracked cwd."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        executor = ShellExecutor(working_dir=tmp_path)
        executor.execute(f"cd {subdir}")

        result = executor.execute("pwd")
        assert str(subdir) in result

    def test_blacklist_blocks_command(self, tmp_path: Path):
        """Blacklisted commands are blocked."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            blacklist=["rm\\s+-rf"],
        )
        result = executor.execute("rm -rf /")
        assert "blocked" in result.lower()

    def test_blacklist_partial_match(self, tmp_path: Path):
        """Blacklist patterns match substrings."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            blacklist=["dangerous"],
        )
        result = executor.execute("echo dangerous_command")
        assert "blocked" in result.lower()

    def test_blacklist_allows_safe(self, tmp_path: Path):
        """Non-matching commands are allowed."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            blacklist=["rm\\s+-rf"],
        )
        result = executor.execute("ls -la")
        assert "blocked" not in result.lower()

    def test_timeout_kills_process(self, tmp_path: Path):
        """Long-running commands are terminated."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            timeout=1,  # 1 second timeout
        )
        result = executor.execute("sleep 10")
        assert "timed out" in result.lower()

    def test_command_error_output(self, tmp_path: Path):
        """Command errors are captured."""
        executor = ShellExecutor(working_dir=tmp_path)
        result = executor.execute("ls /nonexistent_path_12345")
        assert "No such file" in result or "cannot access" in result.lower()

    def test_creates_working_dir(self, tmp_path: Path):
        """Working directory is created if it doesn't exist."""
        new_dir = tmp_path / "new" / "nested" / "dir"
        executor = ShellExecutor(working_dir=new_dir)
        assert new_dir.exists()

    def test_multiline_output(self, tmp_path: Path):
        """Multiline output is captured correctly."""
        executor = ShellExecutor(working_dir=tmp_path)
        result = executor.execute("echo line1; echo line2; echo line3")
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_env_vars(self, tmp_path: Path):
        """Environment variables work."""
        executor = ShellExecutor(working_dir=tmp_path)
        result = executor.execute("export TEST_VAR=hello && echo $TEST_VAR")
        assert "hello" in result

    def test_is_blocked_returns_pattern(self, tmp_path: Path):
        """is_blocked returns the matched pattern."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            blacklist=["rm\\s+-rf", "mkfs"],
        )
        assert executor.is_blocked("rm -rf /") == "rm\\s+-rf"
        assert executor.is_blocked("mkfs /dev/sda") == "mkfs"
        assert executor.is_blocked("ls -la") is None

    def test_per_call_timeout_override(self, tmp_path: Path):
        """Per-call timeout overrides default."""
        executor = ShellExecutor(
            working_dir=tmp_path,
            timeout=10,  # Default 10 seconds
        )
        # Override with 1 second timeout
        result = executor.execute("sleep 5", timeout=1)
        assert "timed out" in result.lower()
        assert "1 seconds" in result

    def test_cd_to_nonexistent_dir_keeps_old_cwd(self, tmp_path: Path):
        """Failed cd command does not corrupt cwd tracking."""
        executor = ShellExecutor(working_dir=tmp_path)

        # Try to cd to nonexistent directory
        executor.execute("cd /nonexistent_path_12345")

        # cwd should remain unchanged
        assert executor.cwd == tmp_path

    def test_heredoc_does_not_poison_cwd(self, tmp_path: Path):
        """Heredoc commands do not corrupt cwd tracking."""
        executor = ShellExecutor(working_dir=tmp_path)
        test_file = tmp_path / "test.txt"

        # Execute a heredoc command
        executor.execute(f"""cat > {test_file} <<EOF
line1
line2
EOF""")

        # cwd should still be valid
        assert executor.cwd == tmp_path
        assert executor.cwd.exists()

        # File should be created with correct content
        assert test_file.exists()
        content = test_file.read_text()
        assert "line1" in content
        assert "line2" in content

    def test_command_output_contains_marker(self, tmp_path: Path):
        """Command outputting marker string does not corrupt cwd."""
        from chat_agent.tools.executor import _CWD_MARKER

        executor = ShellExecutor(working_dir=tmp_path)
        executor.execute(f"echo '{_CWD_MARKER}'")

        # cwd should still be valid
        assert executor.cwd == tmp_path

    def test_path_with_spaces(self, tmp_path: Path):
        """Paths with spaces are handled correctly."""
        space_dir = tmp_path / "path with spaces"
        space_dir.mkdir()

        executor = ShellExecutor(working_dir=tmp_path)
        executor.execute(f"cd '{space_dir}'")

        assert executor.cwd == space_dir

    def test_extra_output_after_marker(self, tmp_path: Path):
        """Extra output between marker and pwd does not corrupt cwd."""
        executor = ShellExecutor(working_dir=tmp_path)

        # Command that produces stderr after the main command
        executor.execute("echo test; echo 'some warning' >&2")

        assert executor.cwd == tmp_path
