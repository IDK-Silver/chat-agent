"""Environment-backed settings for the native Grok proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from .auth import (
    DEFAULT_GROK_API_BASE_URL,
    DEFAULT_GROK_OAUTH_CLIENT_ID,
    DEFAULT_GROK_OAUTH_DISCOVERY_URL,
    DEFAULT_GROK_OAUTH_SCOPE,
    DEFAULT_REFRESH_SKEW_SECONDS,
    GrokTokenStore,
    resolve_token_path,
)


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class GrokProxySettings:
    token_path: Path = field(default_factory=resolve_token_path)
    host: str = "127.0.0.1"
    port: int = 4144
    request_timeout: float = 120.0
    xai_base_url: str = DEFAULT_GROK_API_BASE_URL
    oauth_client_id: str = DEFAULT_GROK_OAUTH_CLIENT_ID
    oauth_scope: str = DEFAULT_GROK_OAUTH_SCOPE
    oauth_discovery_url: str = DEFAULT_GROK_OAUTH_DISCOVERY_URL
    refresh_skew_seconds: int = DEFAULT_REFRESH_SKEW_SECONDS
    user_agent: str = "chat-agent-grok-proxy/0.1.0"
    access_token: str | None = None

    @classmethod
    def from_env(cls) -> "GrokProxySettings":
        settings = cls.for_login_from_env()
        xai_base_url = (
            _env("GROK_PROXY_BASE_URL", "XAI_BASE_URL") or DEFAULT_GROK_API_BASE_URL
        ).rstrip("/")
        access_token = _env("GROK_PROXY_ACCESS_TOKEN")
        if not access_token:
            stored = GrokTokenStore(settings.token_path).load()
            if stored is None:
                raise ValueError(
                    "Grok OAuth token is required. Run `uv run proxy grok login` to create "
                    f"{settings.token_path}, or set GROK_PROXY_ACCESS_TOKEN."
                )
        refresh_skew = int(
            _env("GROK_PROXY_REFRESH_SKEW_SECONDS") or str(DEFAULT_REFRESH_SKEW_SECONDS)
        )
        user_agent = _env("GROK_PROXY_USER_AGENT") or "chat-agent-grok-proxy/0.1.0"
        return cls(
            token_path=settings.token_path,
            host=settings.host,
            port=settings.port,
            request_timeout=settings.request_timeout,
            xai_base_url=xai_base_url,
            oauth_client_id=settings.oauth_client_id,
            oauth_scope=settings.oauth_scope,
            oauth_discovery_url=settings.oauth_discovery_url,
            refresh_skew_seconds=refresh_skew,
            user_agent=user_agent,
            access_token=access_token,
        )

    @classmethod
    def for_login_from_env(cls) -> "GrokProxySettings":
        token_path = resolve_token_path(_env("GROK_PROXY_TOKEN_PATH"))
        host = _env("GROK_PROXY_HOST") or "127.0.0.1"
        port = int(_env("GROK_PROXY_PORT") or "4144")
        request_timeout = float(_env("GROK_PROXY_REQUEST_TIMEOUT") or "120")
        oauth_client_id = _env("GROK_PROXY_CLIENT_ID") or DEFAULT_GROK_OAUTH_CLIENT_ID
        oauth_scope = _env("GROK_PROXY_SCOPE") or DEFAULT_GROK_OAUTH_SCOPE
        oauth_discovery_url = (
            _env("GROK_PROXY_DISCOVERY_URL") or DEFAULT_GROK_OAUTH_DISCOVERY_URL
        )
        return cls(
            token_path=token_path,
            host=host,
            port=port,
            request_timeout=request_timeout,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
            oauth_discovery_url=oauth_discovery_url,
        )
