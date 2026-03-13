"""Tests for chat_supervisor.schema."""

import pytest
from pydantic import ValidationError

from chat_supervisor.schema import (
    ProcessConfig,
    RestartConfig,
    ServerConfig,
    SupervisorConfig,
    UpgradeConfig,
)


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9000

    def test_custom_values(self):
        cfg = ServerConfig(host="0.0.0.0", port=8080)
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080

    def test_rejects_invalid_port(self):
        with pytest.raises(ValidationError):
            ServerConfig(port=0)
        with pytest.raises(ValidationError):
            ServerConfig(port=70000)

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ServerConfig(host="127.0.0.1", unknown="value")


class TestRestartConfig:
    def test_default_none(self):
        cfg = RestartConfig()
        assert cfg.interval_hours is None

    def test_valid_interval(self):
        cfg = RestartConfig(interval_hours=4)
        assert cfg.interval_hours == 4

    def test_rejects_zero_interval(self):
        with pytest.raises(ValidationError):
            RestartConfig(interval_hours=0)


class TestProcessConfig:
    def test_minimal(self):
        cfg = ProcessConfig(command=["echo", "hello"])
        assert cfg.enabled is True
        assert cfg.command == ["echo", "hello"]
        assert cfg.cwd is None
        assert cfg.auto_restart is True
        assert cfg.startup_delay == 0.0
        assert cfg.control_url is None
        assert cfg.shutdown_timeout == 30.0
        assert cfg.join_restart_cycle is False
        assert cfg.depends_on == []
        assert cfg.start_new_session is True

    def test_full_config(self):
        cfg = ProcessConfig(
            enabled=True,
            command=["uv", "run", "chat-cli"],
            cwd="/path/to/dir",
            env={"FOO": "bar"},
            auto_restart=False,
            startup_delay=5.0,
            control_url="http://127.0.0.1:9001",
            shutdown_timeout=60.0,
            join_restart_cycle=True,
            depends_on=["dep1"],
            start_new_session=False,
        )
        assert cfg.control_url == "http://127.0.0.1:9001"
        assert cfg.depends_on == ["dep1"]
        assert cfg.start_new_session is False

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ProcessConfig(command=["echo"], bogus=True)


class TestUpgradeConfig:
    def test_defaults(self):
        cfg = UpgradeConfig()
        assert cfg.auto_check is False
        assert cfg.check_interval_minutes == 30
        assert cfg.branch == "main"
        assert cfg.post_pull == []
        assert cfg.self_watch_paths == []

    def test_auto_check_enabled(self):
        cfg = UpgradeConfig(auto_check=True, check_interval_minutes=15, branch="dev")
        assert cfg.auto_check is True
        assert cfg.check_interval_minutes == 15
        assert cfg.branch == "dev"


class TestSupervisorConfig:
    def test_empty_is_valid(self):
        cfg = SupervisorConfig()
        assert cfg.processes == {}

    def test_full_config(self):
        cfg = SupervisorConfig.model_validate({
            "server": {"host": "0.0.0.0", "port": 9000},
            "restart": {"interval_hours": 4},
            "processes": {
                "copilot-proxy": {
                    "command": ["uv", "run", "copilot-proxy"],
                    "cwd": ".",
                    "startup_delay": 1,
                },
                "chat-cli": {
                    "command": ["uv", "run", "chat-cli"],
                    "depends_on": ["copilot-proxy"],
                    "join_restart_cycle": True,
                    "start_new_session": False,
                },
            },
            "upgrade": {
                "post_pull": ["uv", "sync"],
                "self_watch_paths": ["src/chat_supervisor/"],
            },
        })
        assert len(cfg.processes) == 2
        assert cfg.processes["chat-cli"].depends_on == ["copilot-proxy"]
        assert cfg.processes["chat-cli"].start_new_session is False
