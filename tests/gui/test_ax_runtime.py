"""Tests for gui/ax_runtime.py binary provisioning (no network, no swift)."""

import pytest

from chat_agent.gui import ax_runtime
from chat_agent.gui.ax_runtime import (
    AXRuntimeError,
    DEFAULT_COMMIT,
    binary_path,
    ensure_binary,
)


def test_binary_path_is_commit_scoped(tmp_path):
    p = binary_path("fakecommit0123456789", str(tmp_path))
    assert str(tmp_path) in p
    assert "fakecommit01" in p
    assert p.endswith("OpenComputerUse")


def test_cached_binary_returned_without_toolchain(tmp_path, monkeypatch):
    target = tmp_path / DEFAULT_COMMIT[:12] / "OpenComputerUse"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake binary")
    # No swift/git available: cached path must win before any toolchain check.
    monkeypatch.setattr(ax_runtime.shutil, "which", lambda _: None)
    assert ensure_binary(cache_root=str(tmp_path)) == str(target)


def test_override_path_missing_raises(tmp_path):
    with pytest.raises(AXRuntimeError, match="missing file"):
        ensure_binary(override_path=str(tmp_path / "nope"))


def test_override_path_existing_wins(tmp_path, monkeypatch):
    override = tmp_path / "custom-server"
    override.write_bytes(b"bin")
    monkeypatch.setattr(ax_runtime.shutil, "which", lambda _: None)
    assert ensure_binary(override_path=str(override)) == str(override)


def test_missing_swift_raises_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ax_runtime.shutil, "which", lambda _: None)
    with pytest.raises(AXRuntimeError, match="swift"):
        ensure_binary(cache_root=str(tmp_path))


class _FakeAx:
    def __init__(self, repo=None, commit=None, binary_path=None):
        self.repo = repo
        self.commit = commit
        self.binary_path = binary_path


class _FakeAgent:
    def __init__(self, enabled=True, ax=None):
        self.enabled = enabled
        self.ax = ax or _FakeAx()


class _FakeConfig:
    def __init__(self, agents):
        self.agents = agents


def test_resolve_build_params_defaults_when_config_unreadable():
    assert ax_runtime.resolve_build_params(None) == {}


def test_resolve_build_params_skips_when_gui_disabled():
    cfg = _FakeConfig({"gui_manager": _FakeAgent(enabled=False)})
    assert ax_runtime.resolve_build_params(cfg) is None


def test_resolve_build_params_skips_when_gui_absent():
    assert ax_runtime.resolve_build_params(_FakeConfig({})) is None


def test_resolve_build_params_honors_overrides():
    cfg = _FakeConfig({
        "gui_manager": _FakeAgent(ax=_FakeAx(
            repo="https://example.com/fork.git",
            commit="deadbeef",
            binary_path="/opt/OpenComputerUse",
        )),
    })
    params = ax_runtime.resolve_build_params(cfg)
    assert params == {
        "repo": "https://example.com/fork.git",
        "commit": "deadbeef",
        "override_path": "/opt/OpenComputerUse",
    }
