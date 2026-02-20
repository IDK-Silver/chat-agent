"""Tests for chat_supervisor.process."""

import pytest
from pathlib import Path

from chat_supervisor.process import resolve_cwd, topological_sort
from chat_supervisor.schema import ProcessConfig


class TestResolveCwd:
    def test_none_returns_base(self, tmp_path):
        assert resolve_cwd(None, tmp_path) == tmp_path

    def test_relative_path(self, tmp_path):
        result = resolve_cwd("sub/dir", tmp_path)
        assert result == (tmp_path / "sub" / "dir").resolve()

    def test_dot_relative(self, tmp_path):
        result = resolve_cwd("./copilot-api", tmp_path)
        assert result == (tmp_path / "copilot-api").resolve()

    def test_absolute_path(self, tmp_path):
        abs_path = "/opt/copilot-api"
        result = resolve_cwd(abs_path, tmp_path)
        assert result == Path(abs_path)


class TestTopologicalSort:
    def test_no_dependencies(self):
        procs = {
            "a": ProcessConfig(command=["a"]),
            "b": ProcessConfig(command=["b"]),
        }
        order = topological_sort(procs)
        assert set(order) == {"a", "b"}

    def test_linear_chain(self):
        procs = {
            "c": ProcessConfig(command=["c"], depends_on=["b"]),
            "b": ProcessConfig(command=["b"], depends_on=["a"]),
            "a": ProcessConfig(command=["a"]),
        }
        order = topological_sort(procs)
        assert order.index("a") < order.index("b") < order.index("c")

    def test_copilot_then_chatcli(self):
        procs = {
            "copilot-api": ProcessConfig(command=["npx"]),
            "chat-cli": ProcessConfig(
                command=["uv"], depends_on=["copilot-api"]
            ),
        }
        order = topological_sort(procs)
        assert order == ["copilot-api", "chat-cli"]

    def test_circular_dependency(self):
        procs = {
            "a": ProcessConfig(command=["a"], depends_on=["b"]),
            "b": ProcessConfig(command=["b"], depends_on=["a"]),
        }
        with pytest.raises(ValueError, match="Circular"):
            topological_sort(procs)

    def test_missing_dependency(self):
        procs = {
            "a": ProcessConfig(command=["a"], depends_on=["nonexistent"]),
        }
        with pytest.raises(ValueError, match="not defined"):
            topological_sort(procs)

    def test_disabled_process_skipped(self):
        procs = {
            "a": ProcessConfig(command=["a"], enabled=False),
            "b": ProcessConfig(command=["b"]),
        }
        order = topological_sort(procs)
        assert order == ["b"]
