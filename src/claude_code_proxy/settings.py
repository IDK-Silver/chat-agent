"""Environment-backed settings for the native Claude Code proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from .auth import (
    DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID,
    DEFAULT_CLAUDE_CODE_OAUTH_SCOPE,
    resolve_credentials_path,
    resolve_token_path,
)

DEFAULT_REQUIRED_SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."
DEFAULT_BETA_HEADERS = (
    "claude-code-20250219,"
    "oauth-2025-04-20,"
    "interleaved-thinking-2025-05-14,"
    "fine-grained-tool-streaming-2025-05-14"
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
class ClaudeCodeProxySettings:
    token_path: Path = field(default_factory=resolve_token_path)
    credentials_path: Path | None = field(default_factory=resolve_credentials_path)
    host: str = "127.0.0.1"
    port: int = 4142
    request_timeout: float = 120.0
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    oauth_client_id: str = DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID
    oauth_scope: str = DEFAULT_CLAUDE_CODE_OAUTH_SCOPE
    beta_headers: str = DEFAULT_BETA_HEADERS
    required_system_prompt: str = DEFAULT_REQUIRED_SYSTEM_PROMPT
    user_agent: str = "chat-agent-claude-code-proxy/0.1.0"
    access_token: str | None = None
    allow_claude_code_fallback: bool = False

    @classmethod
    def from_env(cls) -> "ClaudeCodeProxySettings":
        settings = cls.for_login_from_env()
        anthropic_base_url = (
            _env("CLAUDE_CODE_PROXY_ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
        ).rstrip("/")
        beta_headers = _env("CLAUDE_CODE_PROXY_BETA_HEADERS") or DEFAULT_BETA_HEADERS
        anthropic_version = _env("CLAUDE_CODE_PROXY_ANTHROPIC_VERSION") or "2023-06-01"
        required_system_prompt = (
            _env("CLAUDE_CODE_PROXY_REQUIRED_SYSTEM_PROMPT") or DEFAULT_REQUIRED_SYSTEM_PROMPT
        )
        user_agent = _env("CLAUDE_CODE_PROXY_USER_AGENT") or "chat-agent-claude-code-proxy/0.1.0"
        access_token = _env("CLAUDE_CODE_PROXY_ACCESS_TOKEN", "CLAUDE_CODE_ACCESS_TOKEN")
        return cls(
            token_path=settings.token_path,
            credentials_path=settings.credentials_path,
            host=settings.host,
            port=settings.port,
            request_timeout=settings.request_timeout,
            anthropic_base_url=anthropic_base_url,
            anthropic_version=anthropic_version,
            oauth_client_id=settings.oauth_client_id,
            oauth_scope=settings.oauth_scope,
            beta_headers=beta_headers,
            required_system_prompt=required_system_prompt,
            user_agent=user_agent,
            access_token=access_token,
            allow_claude_code_fallback=settings.allow_claude_code_fallback,
        )

    @classmethod
    def for_login_from_env(cls) -> "ClaudeCodeProxySettings":
        host = _env("CLAUDE_CODE_PROXY_HOST") or "127.0.0.1"
        port = int(_env("CLAUDE_CODE_PROXY_PORT") or "4142")
        request_timeout = float(_env("CLAUDE_CODE_PROXY_REQUEST_TIMEOUT") or "120")
        credentials_path = resolve_credentials_path(_env("CLAUDE_CODE_PROXY_CREDENTIALS_PATH"))
        token_path = resolve_token_path(_env("CLAUDE_CODE_PROXY_TOKEN_PATH"))
        oauth_client_id = _env("CLAUDE_CODE_PROXY_CLIENT_ID") or DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID
        oauth_scope = _env("CLAUDE_CODE_PROXY_SCOPE") or DEFAULT_CLAUDE_CODE_OAUTH_SCOPE
        allow_claude_code_fallback = _env_bool(
            "CLAUDE_CODE_PROXY_ENABLE_CLAUDE_CODE_FALLBACK",
            default=credentials_path is not None,
        )
        return cls(
            token_path=token_path,
            credentials_path=credentials_path,
            host=host,
            port=port,
            request_timeout=request_timeout,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
            allow_claude_code_fallback=allow_claude_code_fallback,
        )
