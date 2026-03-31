"""Tests for the native Claude Code proxy transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess

import pytest

from claude_code_proxy.auth import ClaudeCodeCredentialLoader, StoredClaudeCodeToken
from claude_code_proxy.service import (
    EFFORT_BETA_HEADER,
    ClaudeCodeProxyService,
    ClaudeCodeTokenManager,
)
from claude_code_proxy.settings import ClaudeCodeProxySettings
from chat_agent.llm.schema import ClaudeCodeRequest, ClaudeCodeMessagePayload


class _AsyncResponse:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def json(self) -> dict:
        return self._payload


class _AsyncClient:
    def __init__(self, effects: list[dict], calls: list[dict]):
        self._effects = effects
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, headers: dict, json: dict):
        self._calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        effect = self._effects.pop(0)
        return _AsyncResponse(effect)


def _patch_async_httpx(monkeypatch, effects: list[dict], calls: list[dict]) -> None:
    monkeypatch.setattr(
        "claude_code_proxy.service.httpx.AsyncClient",
        lambda timeout: _AsyncClient(effects, calls),
    )


@pytest.mark.asyncio
async def test_proxy_service_injects_required_prompt_and_preserves_cache_control(monkeypatch):
    effects = [{"content": [{"type": "text", "text": "ok"}]}]
    calls: list[dict] = []
    _patch_async_httpx(monkeypatch, effects, calls)
    service = ClaudeCodeProxyService(
        ClaudeCodeProxySettings(access_token="Bearer imported-token")
    )

    request = ClaudeCodeRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[{"type": "text", "text": "[Core Rules]", "cache_control": {"type": "ephemeral"}}],
        messages=[ClaudeCodeMessagePayload(role="user", content="hi")],
    )

    body, media_type = await service.forward_json(request)

    assert media_type == "application/json"
    assert json.loads(body)["content"][0]["text"] == "ok"
    payload = calls[0]["json"]
    assert payload["system"][0]["text"] == "You are Claude Code, Anthropic's official CLI for Claude."
    assert payload["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert calls[0]["headers"]["Authorization"] == "Bearer imported-token"
    assert EFFORT_BETA_HEADER in calls[0]["headers"]["anthropic-beta"].split(",")


@pytest.mark.asyncio
async def test_proxy_service_skips_effort_beta_for_non_effort_model(monkeypatch):
    effects = [{"content": [{"type": "text", "text": "ok"}]}]
    calls: list[dict] = []
    _patch_async_httpx(monkeypatch, effects, calls)
    service = ClaudeCodeProxyService(
        ClaudeCodeProxySettings(access_token="Bearer imported-token")
    )

    request = ClaudeCodeRequest(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[ClaudeCodeMessagePayload(role="user", content="hi")],
    )

    await service.forward_json(request)

    assert EFFORT_BETA_HEADER not in calls[0]["headers"]["anthropic-beta"].split(",")


def test_credential_loader_reads_claude_code_credentials(tmp_path: Path):
    expires_at = int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp() * 1000)
    path = tmp_path / ".credentials.json"
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "access-token",
                    "refreshToken": "refresh-token",
                    "expiresAt": expires_at,
                    "scopes": ["user:file_upload", "user:inference"],
                    "subscriptionType": "max",
                    "rateLimitTier": "default_claude_max_5x",
                }
            }
        )
    )

    loaded = ClaudeCodeCredentialLoader(path=path).load()

    assert loaded is not None
    assert loaded.access_token == "access-token"
    assert loaded.refresh_token == "refresh-token"


def test_credential_loader_reads_claude_code_credentials_from_macos_keychain(monkeypatch):
    expires_at = int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp() * 1000)
    payload = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "access-token",
                "refreshToken": "refresh-token",
                "expiresAt": expires_at,
                "subscriptionType": "max",
            }
        }
    )

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=payload,
            stderr="",
        )

    monkeypatch.setattr("claude_code_proxy.auth.default_credentials_paths", lambda: [])
    monkeypatch.setattr("claude_code_proxy.auth.sys.platform", "darwin")
    monkeypatch.setattr("claude_code_proxy.auth.subprocess.run", _fake_run)

    loaded = ClaudeCodeCredentialLoader().load()

    assert loaded is not None
    assert loaded.access_token == "access-token"
    assert loaded.refresh_token == "refresh-token"


@pytest.mark.asyncio
async def test_token_manager_skips_claude_code_fallback_by_default(monkeypatch, tmp_path: Path):
    settings = ClaudeCodeProxySettings(token_path=tmp_path / "token.json")
    called = False

    def _unexpected_load(self):
        nonlocal called
        called = True
        raise AssertionError("Claude Code fallback should be disabled by default")

    monkeypatch.setattr("claude_code_proxy.service.ClaudeCodeCredentialLoader.load", _unexpected_load)

    with pytest.raises(RuntimeError, match="claude-code-proxy login"):
        await ClaudeCodeTokenManager(settings).get_token()

    assert called is False


@pytest.mark.asyncio
async def test_token_manager_uses_claude_code_fallback_when_enabled(monkeypatch, tmp_path: Path):
    settings = ClaudeCodeProxySettings(
        token_path=tmp_path / "token.json",
        allow_claude_code_fallback=True,
    )

    monkeypatch.setattr(
        "claude_code_proxy.service.ClaudeCodeCredentialLoader.load",
        lambda self: StoredClaudeCodeToken(
            access_token="imported-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            source="imported_claude_code_credentials",
            client_id="client-id",
            created_at=datetime.now(tz=UTC),
        ),
    )

    token = await ClaudeCodeTokenManager(settings).get_token()

    assert token == "imported-token"
