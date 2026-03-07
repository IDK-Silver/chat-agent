"""Tests for chat_supervisor.config."""

import pytest
import yaml

from chat_supervisor import config as config_module
from chat_supervisor.config import load_supervisor_config


def test_load_supervisor_config(tmp_path, monkeypatch):
    cfg_data = {
        "server": {"host": "0.0.0.0", "port": 8888},
        "restart": {"interval_hours": 2},
        "processes": {
            "test-proc": {
                "command": ["echo", "hello"],
                "start_new_session": False,
            },
        },
    }
    (tmp_path / "test.yaml").write_text(yaml.dump(cfg_data))
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    result = load_supervisor_config("test.yaml")
    assert result.server.port == 8888
    assert result.restart.interval_hours == 2
    assert "test-proc" in result.processes
    assert result.processes["test-proc"].start_new_session is False


def test_load_supervisor_config_empty(tmp_path, monkeypatch):
    (tmp_path / "empty.yaml").write_text("")
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    result = load_supervisor_config("empty.yaml")
    assert result.processes == {}


def test_load_supervisor_config_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        load_supervisor_config("nonexistent.yaml")
