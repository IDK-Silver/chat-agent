"""Environment-backed settings for the native Codex proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from .auth import (
    DEFAULT_CODEX_OAUTH_AUTHORIZE_URL,
    DEFAULT_CODEX_OAUTH_CLIENT_ID,
    DEFAULT_CODEX_OAUTH_REDIRECT_URI,
    DEFAULT_CODEX_OAUTH_SCOPE,
    DEFAULT_CODEX_OAUTH_TOKEN_URL,
    resolve_codex_auth_path,
    resolve_token_path,
)


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CodexProxySettings:
    token_path: Path = field(default_factory=resolve_token_path)
    codex_auth_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 4143
    request_timeout: float = 120.0
    codex_base_url: str = "https://chatgpt.com/backend-api"
    oauth_authorize_url: str = DEFAULT_CODEX_OAUTH_AUTHORIZE_URL
    oauth_token_url: str = DEFAULT_CODEX_OAUTH_TOKEN_URL
    oauth_redirect_uri: str = DEFAULT_CODEX_OAUTH_REDIRECT_URI
    oauth_client_id: str = DEFAULT_CODEX_OAUTH_CLIENT_ID
    oauth_scope: str = DEFAULT_CODEX_OAUTH_SCOPE
    access_token: str | None = None
    allow_codex_auth_fallback: bool = False

    @classmethod
    def from_env(cls) -> "CodexProxySettings":
        settings = cls.for_login_from_env()
        access_token = _env("CODEX_PROXY_ACCESS_TOKEN")
        codex_base_url = (_env("CODEX_PROXY_BASE_URL") or "https://chatgpt.com/backend-api").rstrip("/")
        oauth_authorize_url = _env("CODEX_PROXY_AUTHORIZE_URL") or DEFAULT_CODEX_OAUTH_AUTHORIZE_URL
        oauth_token_url = _env("CODEX_PROXY_TOKEN_URL") or DEFAULT_CODEX_OAUTH_TOKEN_URL
        oauth_redirect_uri = _env("CODEX_PROXY_REDIRECT_URI") or DEFAULT_CODEX_OAUTH_REDIRECT_URI
        return cls(
            token_path=settings.token_path,
            codex_auth_path=settings.codex_auth_path,
            host=settings.host,
            port=settings.port,
            request_timeout=settings.request_timeout,
            codex_base_url=codex_base_url,
            oauth_authorize_url=oauth_authorize_url,
            oauth_token_url=oauth_token_url,
            oauth_redirect_uri=oauth_redirect_uri,
            oauth_client_id=settings.oauth_client_id,
            oauth_scope=settings.oauth_scope,
            access_token=access_token,
            allow_codex_auth_fallback=settings.allow_codex_auth_fallback,
        )

    @classmethod
    def for_login_from_env(cls) -> "CodexProxySettings":
        host = _env("CODEX_PROXY_HOST") or "127.0.0.1"
        port = int(_env("CODEX_PROXY_PORT") or "4143")
        request_timeout = float(_env("CODEX_PROXY_REQUEST_TIMEOUT") or "120")
        token_path = resolve_token_path(_env("CODEX_PROXY_TOKEN_PATH"))
        raw_codex_auth_path = _env("CODEX_PROXY_CODEX_AUTH_PATH")
        codex_auth_path = (
            resolve_codex_auth_path(raw_codex_auth_path)
            if raw_codex_auth_path is not None
            else None
        )
        oauth_authorize_url = _env("CODEX_PROXY_AUTHORIZE_URL") or DEFAULT_CODEX_OAUTH_AUTHORIZE_URL
        oauth_token_url = _env("CODEX_PROXY_TOKEN_URL") or DEFAULT_CODEX_OAUTH_TOKEN_URL
        oauth_redirect_uri = _env("CODEX_PROXY_REDIRECT_URI") or DEFAULT_CODEX_OAUTH_REDIRECT_URI
        oauth_client_id = _env("CODEX_PROXY_CLIENT_ID") or DEFAULT_CODEX_OAUTH_CLIENT_ID
        oauth_scope = _env("CODEX_PROXY_SCOPE") or DEFAULT_CODEX_OAUTH_SCOPE
        allow_codex_auth_fallback = _env_bool(
            "CODEX_PROXY_ENABLE_CODEX_AUTH_FALLBACK",
            default=codex_auth_path is not None,
        )
        return cls(
            token_path=token_path,
            codex_auth_path=codex_auth_path,
            host=host,
            port=port,
            request_timeout=request_timeout,
            oauth_authorize_url=oauth_authorize_url,
            oauth_token_url=oauth_token_url,
            oauth_redirect_uri=oauth_redirect_uri,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
            allow_codex_auth_fallback=allow_codex_auth_fallback,
        )
