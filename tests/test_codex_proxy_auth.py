"""Tests for Codex proxy login flows."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from codex_proxy.__main__ import run_login
from codex_proxy.auth import (
    CodexAuthLoader,
    CodexBrowserAuthorization,
    CodexOAuthClient,
    CodexTokenStore,
    StoredCodexToken,
    extract_chatgpt_account_id,
)
from codex_proxy.settings import CodexProxySettings


def _make_fake_jwt(*, account_id: str = "acct_123", exp: int = 2_200_000_000) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
    }

    def _encode(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_encode(header)}.{_encode(payload)}.signature"


class _SyncResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    @property
    def text(self) -> str:
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

    def post(self, url: str, headers: dict, data: dict):
        self._calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "data": data,
            }
        )
        return _SyncResponse(self._effects.pop(0))


def _patch_sync_httpx(monkeypatch, effects: list[dict], calls: list[dict]) -> None:
    monkeypatch.setattr(
        "codex_proxy.auth.httpx.Client",
        lambda timeout: _SyncClient(effects, calls),
    )


def _stored_token(*, access_token: str, source: str) -> StoredCodexToken:
    return StoredCodexToken(
        access_token=access_token,
        refresh_token="refresh-token",
        account_id=extract_chatgpt_account_id(access_token),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        source=source,
        client_id="client-id",
        created_at=datetime.now(tz=UTC),
    )


def test_token_store_round_trip(tmp_path: Path):
    path = tmp_path / "token.json"
    store = CodexTokenStore(path)
    token = StoredCodexToken(
        access_token=_make_fake_jwt(),
        refresh_token="refresh-token",
        account_id="acct_123",
        expires_at="2030-01-01T00:00:00Z",
        source="imported_codex_auth",
        client_id="client-id",
        created_at="2026-04-11T00:00:00Z",
    )

    store.save(token)
    loaded = store.load()

    assert loaded is not None
    assert loaded.account_id == "acct_123"
    assert loaded.refresh_token == "refresh-token"


def test_codex_auth_loader_reads_official_auth_json(tmp_path: Path):
    auth_path = tmp_path / "auth.json"
    access_token = _make_fake_jwt(account_id="acct_loader")
    auth_path.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": None,
                "auth_mode": "chatgpt",
                "last_refresh": "2026-04-11T01:02:03Z",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "refresh-loader",
                },
            }
        )
    )

    loaded = CodexAuthLoader(path=auth_path).load()

    assert loaded is not None
    assert loaded.account_id == "acct_loader"
    assert loaded.refresh_token == "refresh-loader"
    assert extract_chatgpt_account_id(loaded.access_token) == "acct_loader"


def test_settings_from_env_does_not_load_saved_token(monkeypatch, tmp_path: Path):
    token_path = tmp_path / "token.json"
    CodexTokenStore(token_path).save(
        StoredCodexToken(
            access_token=_make_fake_jwt(account_id="acct_saved"),
            refresh_token="refresh-saved",
            account_id="acct_saved",
            expires_at="2030-01-01T00:00:00Z",
            source="imported_codex_auth",
            client_id="client-id",
            created_at="2026-04-11T00:00:00Z",
        )
    )
    monkeypatch.setenv("CODEX_PROXY_TOKEN_PATH", str(token_path))
    monkeypatch.delenv("CODEX_PROXY_ACCESS_TOKEN", raising=False)

    settings = CodexProxySettings.from_env()

    assert settings.access_token is None
    assert settings.token_path == token_path
    assert settings.allow_codex_auth_fallback is False


def test_oauth_client_builds_authorization_url_and_exchanges_code(monkeypatch):
    effects = [
        {
            "access_token": _make_fake_jwt(account_id="acct_oauth"),
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }
    ]
    calls: list[dict] = []
    _patch_sync_httpx(monkeypatch, effects, calls)

    client = CodexOAuthClient(
        request_timeout=30.0,
        client_id="client-id",
        scope="openid profile email offline_access",
    )
    authorization = client.begin_authorization()
    parsed = urlparse(authorization.authorization_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    assert query["client_id"] == ["client-id"]
    assert query["scope"] == ["openid profile email offline_access"]
    assert query["state"] == [authorization.state]
    assert query["originator"] == ["codex_cli_rs"]

    stored = client.exchange_callback_code(
        "auth-code",
        returned_state=authorization.state,
        authorization=authorization,
    )

    assert stored.account_id == "acct_oauth"
    assert stored.source == "oauth_browser"
    assert calls[0]["url"] == "https://auth.openai.com/oauth/token"
    assert calls[0]["data"]["grant_type"] == "authorization_code"


def test_run_login_saves_browser_oauth_token(monkeypatch, tmp_path: Path):
    token_path = tmp_path / "token.json"
    settings = CodexProxySettings(token_path=token_path)
    access_token = _make_fake_jwt(account_id="acct_browser")

    class _FakeOAuthClient:
        def begin_authorization(self):
            return CodexBrowserAuthorization(
                authorization_url="https://auth.openai.com/oauth/authorize?state=state-1",
                code_verifier="verifier-1",
                state="state-1",
                redirect_uri="http://localhost:1455/auth/callback",
            )

        def exchange_callback_code(self, code, *, returned_state, authorization):
            assert code == "auth-code"
            assert returned_state == "state-1"
            assert authorization.state == "state-1"
            return _stored_token(access_token=access_token, source="oauth_browser")

    monkeypatch.setattr(
        "codex_proxy.__main__.CodexProxySettings.for_login_from_env",
        lambda: settings,
    )
    monkeypatch.setattr(
        "codex_proxy.__main__._build_oauth_client",
        lambda _settings: _FakeOAuthClient(),
    )
    monkeypatch.setattr(
        "codex_proxy.__main__.wait_for_browser_callback",
        lambda authorization, on_ready=None: ("auth-code", authorization.state),
    )
    monkeypatch.setattr("codex_proxy.__main__.webbrowser.open", lambda _url: True)

    result = run_login(
        SimpleNamespace(
            token_path=None,
            codex_auth_path=None,
            client_id=None,
            scope=None,
            from_codex=False,
            no_open_browser=False,
        )
    )

    saved = CodexTokenStore(token_path).load()
    assert result == 0
    assert saved is not None
    assert saved.account_id == "acct_browser"
    assert saved.source == "oauth_browser"


def test_run_login_imports_official_codex_auth(monkeypatch, tmp_path: Path):
    token_path = tmp_path / "token.json"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "last_refresh": "2026-04-11T01:02:03Z",
                "tokens": {
                    "access_token": _make_fake_jwt(account_id="acct_login"),
                    "refresh_token": "refresh-login",
                },
            }
        )
    )
    settings = CodexProxySettings(
        token_path=token_path,
        codex_auth_path=auth_path,
    )

    monkeypatch.setattr(
        "codex_proxy.__main__.CodexProxySettings.for_login_from_env",
        lambda: settings,
    )

    result = run_login(
        SimpleNamespace(
            token_path=None,
            codex_auth_path=None,
            client_id=None,
            scope=None,
            from_codex=True,
            no_open_browser=True,
        )
    )

    saved = CodexTokenStore(token_path).load()
    assert result == 0
    assert saved is not None
    assert saved.account_id == "acct_login"
    assert saved.source == "imported_codex_auth"


def test_run_login_fails_when_codex_auth_missing(monkeypatch, tmp_path: Path, capsys):
    settings = CodexProxySettings(
        token_path=tmp_path / "token.json",
        codex_auth_path=tmp_path / "missing-auth.json",
    )
    monkeypatch.setattr(
        "codex_proxy.__main__.CodexProxySettings.for_login_from_env",
        lambda: settings,
    )

    result = run_login(
        SimpleNamespace(
            token_path=None,
            codex_auth_path=None,
            client_id=None,
            scope=None,
            from_codex=True,
            no_open_browser=True,
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "No Codex auth found" in captured.err
