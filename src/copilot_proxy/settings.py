"""Environment-backed settings for the native Copilot proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal

from .auth import (
    DEFAULT_COPILOT_DEVICE_SCOPE,
    DEFAULT_COPILOT_OAUTH_CLIENT_ID,
    GitHubTokenStore,
    resolve_token_path,
)


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class CopilotProxySettings:
    github_token: str
    token_path: Path = field(default_factory=resolve_token_path)
    host: str = "127.0.0.1"
    port: int = 4141
    account_type: Literal["individual", "business", "enterprise"] = "individual"
    enterprise_domain: str | None = None
    request_timeout: float = 120.0
    oauth_client_id: str = DEFAULT_COPILOT_OAUTH_CLIENT_ID
    oauth_scope: str = DEFAULT_COPILOT_DEVICE_SCOPE
    editor_version: str = "1.110.1"
    copilot_version: str = "0.40.2026031301"
    api_version: str = "2025-10-01"

    @classmethod
    def from_env(cls) -> "CopilotProxySettings":
        settings = cls.for_login_from_env()
        github_token = _env(
            "COPILOT_PROXY_GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        )
        if not github_token:
            stored = GitHubTokenStore(settings.token_path).load()
            github_token = stored.github_token if stored is not None else None
        if not github_token:
            raise ValueError(
                "GitHub token is required. Set COPILOT_PROXY_GITHUB_TOKEN, GH_TOKEN, or "
                f"GITHUB_TOKEN, or run `uv run copilot-proxy login` to create {settings.token_path}."
            )
        return cls(
            github_token=github_token,
            token_path=settings.token_path,
            host=settings.host,
            port=settings.port,
            account_type=settings.account_type,
            enterprise_domain=settings.enterprise_domain,
            request_timeout=settings.request_timeout,
            oauth_client_id=settings.oauth_client_id,
            oauth_scope=settings.oauth_scope,
            editor_version=settings.editor_version,
            copilot_version=settings.copilot_version,
            api_version=settings.api_version,
        )

    @classmethod
    def for_login_from_env(cls) -> "CopilotProxySettings":
        token_path = resolve_token_path(_env("COPILOT_PROXY_TOKEN_PATH"))
        host = _env("COPILOT_PROXY_HOST") or "127.0.0.1"
        port = int(_env("COPILOT_PROXY_PORT") or "4141")
        account_type = _env("COPILOT_PROXY_ACCOUNT_TYPE") or "individual"
        if account_type not in {"individual", "business", "enterprise"}:
            raise ValueError(
                "COPILOT_PROXY_ACCOUNT_TYPE must be one of: individual, business, enterprise"
            )
        enterprise_domain = _env(
            "COPILOT_PROXY_ENTERPRISE_URL",
            "COPILOT_API_ENTERPRISE_URL",
        )
        timeout = float(_env("COPILOT_PROXY_REQUEST_TIMEOUT") or "120")
        oauth_client_id = _env("COPILOT_PROXY_CLIENT_ID") or DEFAULT_COPILOT_OAUTH_CLIENT_ID
        oauth_scope = _env("COPILOT_PROXY_DEVICE_SCOPE") or DEFAULT_COPILOT_DEVICE_SCOPE
        return cls(
            github_token="",
            token_path=token_path,
            host=host,
            port=port,
            account_type=account_type,
            enterprise_domain=enterprise_domain,
            request_timeout=timeout,
            oauth_client_id=oauth_client_id,
            oauth_scope=oauth_scope,
        )

    @property
    def github_web_base_url(self) -> str:
        if self.enterprise_domain:
            return f"https://{self._normalized_enterprise_domain()}"
        return "https://github.com"

    @property
    def github_api_base_url(self) -> str:
        if self.enterprise_domain:
            return f"{self.github_web_base_url}/api/v3"
        return "https://api.github.com"

    @property
    def copilot_base_url(self) -> str:
        if self.enterprise_domain:
            return f"https://copilot-api.{self._normalized_enterprise_domain()}"
        if self.account_type == "individual":
            return "https://api.githubcopilot.com"
        return f"https://api.{self.account_type}.githubcopilot.com"

    @property
    def editor_plugin_version(self) -> str:
        return f"copilot-chat/{self.copilot_version}"

    @property
    def user_agent(self) -> str:
        return f"GitHubCopilotChat/{self.copilot_version}"

    def _normalized_enterprise_domain(self) -> str:
        assert self.enterprise_domain is not None
        return (
            self.enterprise_domain.strip()
            .removeprefix("https://")
            .removeprefix("http://")
            .rstrip("/")
        )
