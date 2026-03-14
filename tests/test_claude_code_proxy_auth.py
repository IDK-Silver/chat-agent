"""Tests for Claude Code proxy login flows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from claude_code_proxy.__main__ import run_login
from claude_code_proxy.auth import (
    ClaudeCodeBrowserAuthorization,
    ClaudeCodeOAuthClient,
    ClaudeCodeTokenStore,
    StoredClaudeCodeToken,
)
from claude_code_proxy.settings import ClaudeCodeProxySettings


class _SyncResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    @property
    def text(self) -> str:
        import json

        return json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _SyncClient:
    def __init__(self, effects: list[dict], calls: list[dict]):
        self._effects = effects
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, headers: dict, json: dict):
        self._calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "json": json,
            }
        )
        return _SyncResponse(self._effects.pop(0))


def _patch_sync_httpx(monkeypatch, effects: list[dict], calls: list[dict]) -> None:
    monkeypatch.setattr(
        "claude_code_proxy.auth.httpx.Client",
        lambda timeout: _SyncClient(effects, calls),
    )


def _stored_token(*, access_token: str, source: str) -> StoredClaudeCodeToken:
    return StoredClaudeCodeToken(
        access_token=access_token,
        refresh_token="refresh-token",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        source=source,
        client_id="client-id",
        created_at=datetime.now(tz=UTC),
    )


def test_oauth_client_builds_authorization_url_and_exchanges_code(monkeypatch):
    effects = [
        {
            "access_token": "oauth-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }
    ]
    calls: list[dict] = []
    _patch_sync_httpx(monkeypatch, effects, calls)

    client = ClaudeCodeOAuthClient(
        request_timeout=30.0,
        client_id="client-id",
        scope="user:profile user:inference",
    )
    authorization = client.begin_authorization()
    parsed = urlparse(authorization.authorization_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "claude.ai"
    assert parsed.path == "/oauth/authorize"
    assert query["client_id"] == ["client-id"]
    assert query["scope"] == ["user:profile user:inference"]
    assert query["state"] == [authorization.state]

    stored = client.exchange_manual_code(
        f"auth-code#{authorization.state}",
        authorization=authorization,
    )

    assert stored.access_token == "oauth-token"
    assert stored.source == "oauth_browser"
    assert calls[0]["url"] == "https://console.anthropic.com/v1/oauth/token"
    assert calls[0]["json"]["grant_type"] == "authorization_code"
    assert calls[0]["json"]["state"] == authorization.state


def test_run_login_saves_browser_oauth_token(monkeypatch, tmp_path: Path):
    token_path = tmp_path / "token.json"
    settings = ClaudeCodeProxySettings(token_path=token_path)

    class _FakeOAuthClient:
        def begin_authorization(self):
            return ClaudeCodeBrowserAuthorization(
                authorization_url="https://claude.ai/oauth/authorize?state=state-1",
                code_verifier="verifier-1",
                state="state-1",
            )

        def exchange_manual_code(self, manual_code, *, authorization):
            assert manual_code == "auth-code#state-1"
            assert authorization.state == "state-1"
            return _stored_token(access_token="oauth-token", source="oauth_browser")

    monkeypatch.setattr(
        "claude_code_proxy.__main__.ClaudeCodeProxySettings.for_login_from_env",
        lambda: settings,
    )
    monkeypatch.setattr(
        "claude_code_proxy.__main__._build_oauth_client",
        lambda _settings: _FakeOAuthClient(),
    )
    monkeypatch.setattr("claude_code_proxy.__main__.webbrowser.open", lambda _url: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "auth-code#state-1")

    result = run_login(
        SimpleNamespace(
            token_path=None,
            credentials_path=None,
            client_id=None,
            scope=None,
            code=None,
            from_claude_code=False,
            no_open_browser=False,
        )
    )

    saved = ClaudeCodeTokenStore(token_path).load()
    assert result == 0
    assert saved is not None
    assert saved.access_token == "oauth-token"
    assert saved.source == "oauth_browser"


def test_run_login_imports_claude_code_token(monkeypatch, tmp_path: Path):
    token_path = tmp_path / "token.json"
    settings = ClaudeCodeProxySettings(token_path=token_path)

    monkeypatch.setattr(
        "claude_code_proxy.__main__.ClaudeCodeProxySettings.for_login_from_env",
        lambda: settings,
    )
    monkeypatch.setattr(
        "claude_code_proxy.__main__.ClaudeCodeCredentialLoader.load",
        lambda self: _stored_token(
            access_token="imported-token",
            source="imported_claude_code_credentials",
        ),
    )

    result = run_login(
        SimpleNamespace(
            token_path=None,
            credentials_path=None,
            client_id=None,
            scope=None,
            code=None,
            from_claude_code=True,
            no_open_browser=True,
        )
    )

    saved = ClaudeCodeTokenStore(token_path).load()
    assert result == 0
    assert saved is not None
    assert saved.access_token == "imported-token"
    assert saved.source == "imported_claude_code_credentials"
