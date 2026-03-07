"""Tests for chat_supervisor.upgrade."""

from unittest.mock import patch, MagicMock

from chat_supervisor.upgrade import (
    has_remote_changes,
    pull_and_post,
    self_restart,
    snapshot_watch_paths,
)
from chat_supervisor.schema import UpgradeConfig


class TestHasRemoteChanges:
    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_returns_true_when_heads_differ(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch
            MagicMock(returncode=0, stdout="aaa\n"),  # local HEAD
            MagicMock(returncode=0, stdout="bbb\n"),  # remote HEAD
        ]
        assert has_remote_changes("main") is True

    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_returns_false_when_heads_match(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch
            MagicMock(returncode=0, stdout="aaa\n"),  # local HEAD
            MagicMock(returncode=0, stdout="aaa\n"),  # remote HEAD
        ]
        assert has_remote_changes("main") is False

    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_returns_false_on_fetch_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="network error")
        assert has_remote_changes("main") is False


class TestPullAndPost:
    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_success_no_post_pull(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        cfg = UpgradeConfig()
        ok, err = pull_and_post(cfg)
        assert ok is True
        assert err == ""

    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_success_with_post_pull(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        cfg = UpgradeConfig(post_pull=["uv", "sync"])
        ok, err = pull_and_post(cfg)
        assert ok is True
        assert mock_run.call_count == 2

    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_git_pull_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="conflict")
        cfg = UpgradeConfig()
        ok, err = pull_and_post(cfg)
        assert ok is False
        assert "git pull failed" in err

    @patch("chat_supervisor.upgrade.subprocess.run")
    def test_post_pull_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git pull ok
            MagicMock(returncode=1, stderr="build error"),  # post_pull fail
        ]
        cfg = UpgradeConfig(post_pull=["make", "build"])
        ok, err = pull_and_post(cfg)
        assert ok is False
        assert "post_pull failed" in err


class TestSnapshotWatchPaths:
    def test_empty_paths(self):
        assert snapshot_watch_paths([]) == {}

    def test_nonexistent_path(self):
        assert snapshot_watch_paths(["/nonexistent/path"]) == {}

    def test_file_path(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        result = snapshot_watch_paths([str(f)])
        assert str(f) in result

    def test_directory_path(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1")
        (tmp_path / "readme.txt").write_text("not python")
        result = snapshot_watch_paths([str(tmp_path)])
        assert any("mod.py" in k for k in result)
        # Only .py files
        assert not any("readme.txt" in k for k in result)


class TestSelfRestart:
    @patch("chat_supervisor.upgrade.os.execv")
    @patch("chat_supervisor.upgrade.sys.executable", "/usr/bin/python3")
    def test_execs_with_start_subcommand(self, mock_execv):
        self_restart()
        mock_execv.assert_called_once_with(
            "/usr/bin/python3",
            ["/usr/bin/python3", "-m", "chat_supervisor", "start"],
        )
