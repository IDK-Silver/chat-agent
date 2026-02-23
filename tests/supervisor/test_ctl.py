"""Tests for chat_supervisor.ctl."""

import json

from chat_supervisor import ctl


def test_stop_command_calls_shutdown(monkeypatch, capsys):
    monkeypatch.setattr(
        ctl,
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

    monkeypatch.setattr(ctl, "_request_json", fake_request_json)

    code = ctl.main(["stop"])
    out = capsys.readouterr().out

    assert code == 0
    assert seen["base_url"] == "http://127.0.0.1:9000"
    assert seen["method"] == "POST"
    assert seen["path"] == "/shutdown"
    assert json.loads(out) == {"status": "shutting_down"}


def test_restart_command_calls_named_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(
        ctl,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    seen = {}

    def fake_request_json(base_url, method, path, timeout=10.0):
        seen["method"] = method
        seen["path"] = path
        return 200, {"status": "restarted"}

    monkeypatch.setattr(ctl, "_request_json", fake_request_json)

    code = ctl.main(["restart", "chat-cli"])
    _ = capsys.readouterr()

    assert code == 0
    assert seen == {"method": "POST", "path": "/restart/chat-cli"}


def test_status_command_returns_nonzero_on_http_error(monkeypatch, capsys):
    monkeypatch.setattr(
        ctl,
        "_resolve_base_url",
        lambda config_name, host, port: "http://127.0.0.1:9000",
    )
    monkeypatch.setattr(
        ctl,
        "_request_json",
        lambda base_url, method, path, timeout=10.0: (500, {"error": "boom"}),
    )

    code = ctl.main(["status"])
    out = capsys.readouterr().out

    assert code == 1
    assert json.loads(out) == {"error": "boom"}
