"""Tests for unified chat-supervisor CLI."""

import json

import pytest

import chat_supervisor.__main__ as main_mod


def test_stop_command_calls_shutdown(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen.update(
            {
                "base_url": base_url,
                "method": method,
                "path": path,
                "timeout": timeout,
            }
        )
        return 200, {"status": "shutting_down"}

    monkeypatch.setattr(main_mod, "_request_json", fake_request_json)

    code = main_mod.main(["stop"])
    out = capsys.readouterr().out

    assert code == 0
    assert seen["base_url"] == "http://127.0.0.1:9000"
    assert seen["method"] == "POST"
    assert seen["path"] == "/shutdown"
    assert json.loads(out) == {"status": "shutting_down"}


def test_restart_command_calls_stack_endpoint_when_name_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen["method"] = method
        seen["path"] = path
        return 200, {"status": "restarted"}

    monkeypatch.setattr(main_mod, "_request_json", fake_request_json)

    code = main_mod.main(["restart"])
    _ = capsys.readouterr()

    assert code == 0
    assert seen == {"method": "POST", "path": "/restart"}


def test_restart_command_calls_named_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen["method"] = method
        seen["path"] = path
        return 200, {"status": "restarted"}

    monkeypatch.setattr(main_mod, "_request_json", fake_request_json)

    code = main_mod.main(["restart", "chat-cli"])
    _ = capsys.readouterr()

    assert code == 0
    assert seen == {"method": "POST", "path": "/restart/chat-cli"}


def test_new_session_command_calls_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen["method"] = method
        seen["path"] = path
        return 200, {"status": "new_session_requested"}

    monkeypatch.setattr(main_mod, "_request_json", fake_request_json)

    code = main_mod.main(["new-session"])
    out = capsys.readouterr().out

    assert code == 0
    assert seen == {"method": "POST", "path": "/new-session"}
    assert json.loads(out) == {"status": "new_session_requested"}


def test_status_command_returns_nonzero_on_http_error(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    monkeypatch.setattr(
        main_mod,
        "_request_json",
        lambda base_url, method, path, timeout=10.0: (500, {"error": "boom"}),
    )

    code = main_mod.main(["status"])
    out = capsys.readouterr().out

    assert code == 1
    assert json.loads(out) == {"error": "boom"}


def test_reload_command_calls_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(
        main_mod,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen["method"] = method
        seen["path"] = path
        return 200, {"status": "reload_requested"}

    monkeypatch.setattr(main_mod, "_request_json", fake_request_json)

    code = main_mod.main(["reload"])
    out = capsys.readouterr().out

    assert code == 0
    assert seen == {"method": "POST", "path": "/reload"}
    assert json.loads(out) == {"status": "reload_requested"}


def test_start_command_runs_supervisor(monkeypatch):
    called = {}

    async def fake_supervisor_run(config_path):
        called["config"] = config_path

    monkeypatch.setattr(main_mod, "_run", fake_supervisor_run)

    code = main_mod.main(["start", "--config", "custom.yaml"])

    assert code == 0
    assert called == {"config": "custom.yaml"}


def test_subcommand_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main_mod.main([])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "required" in err
