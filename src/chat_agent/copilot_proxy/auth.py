"""GitHub device-flow login helpers for the native Copilot proxy."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_COPILOT_OAUTH_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_COPILOT_DEVICE_SCOPE = "read:user"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredGitHubToken(_StrictModel):
    """Persisted GitHub access token used for Copilot token exchange."""

    version: int = 1
    github_token: str = Field(min_length=1)
    token_type: str = "bearer"
    scope: str | None = None
    source: Literal["oauth_device_flow"] = "oauth_device_flow"
    client_id: str = Field(min_length=1)
    created_at: datetime
    github_login: str | None = None


class GitHubDeviceCode(_StrictModel):
    """Device-flow verification payload returned by GitHub."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = Field(ge=1)
    interval: int = Field(default=5, ge=1)


class GitHubAccessToken(_StrictModel):
    """Access token returned by the OAuth device flow."""

    access_token: str = Field(min_length=1)
    token_type: str = "bearer"
    scope: str | None = None


class GitHubOAuthError(_StrictModel):
    """Structured error payload returned by GitHub OAuth endpoints."""

    error: str
    error_description: str | None = None
    error_uri: str | None = None
    interval: int | None = Field(default=None, ge=1)


class DeviceAuthorizationError(RuntimeError):
    """Raised when the device flow cannot complete."""


def default_token_path() -> Path:
    """Return the platform-appropriate default token store location."""

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "chat-agent" / "copilot-proxy" / "github-token.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chat-agent"
            / "copilot-proxy"
            / "github-token.json"
        )
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "chat-agent" / "copilot-proxy" / "github-token.json"


def resolve_token_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve and expand the token path override if provided."""

    if path is None:
        return default_token_path()
    return Path(path).expanduser()


class GitHubTokenStore:
    """Load and save GitHub access tokens for Copilot proxy auth."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StoredGitHubToken | None:
        if not self.path.is_file():
            return None
        try:
            return StoredGitHubToken.model_validate_json(self.path.read_text())
        except (OSError, ValidationError) as exc:
            raise ValueError(
                f"Invalid Copilot token store at {self.path}: {exc}"
            ) from exc

    def save(self, token: StoredGitHubToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(token.model_dump_json(indent=2))
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)


class GitHubDeviceFlowClient:
    """Run GitHub OAuth device flow and verify Copilot token exchange."""

    def __init__(
        self,
        *,
        auth_base_url: str,
        github_api_base_url: str,
        client_id: str,
        scope: str,
        request_timeout: float,
        editor_version: str,
        editor_plugin_version: str,
        user_agent: str,
        api_version: str,
    ):
        self._auth_base_url = auth_base_url.rstrip("/")
        self._github_api_base_url = github_api_base_url.rstrip("/")
        self._client_id = client_id
        self._scope = scope
        self._request_timeout = request_timeout
        self._editor_version = editor_version
        self._editor_plugin_version = editor_plugin_version
        self._user_agent = user_agent
        self._api_version = api_version

    def request_device_code(self) -> GitHubDeviceCode:
        with httpx.Client(timeout=self._request_timeout) as client:
            response = client.post(
                f"{self._auth_base_url}/login/device/code",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "scope": self._scope,
                },
            )
        try:
            return GitHubDeviceCode.model_validate(self._parse_json(response))
        except ValidationError as exc:
            raise DeviceAuthorizationError(
                f"GitHub returned unexpected device-code payload: {exc}"
            ) from exc

    def poll_access_token(self, device_code: GitHubDeviceCode) -> GitHubAccessToken:
        interval = device_code.interval
        deadline = time.monotonic() + device_code.expires_in

        while time.monotonic() < deadline:
            time.sleep(interval)
            with httpx.Client(timeout=self._request_timeout) as client:
                response = client.post(
                    f"{self._auth_base_url}/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": self._client_id,
                        "device_code": device_code.device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            payload = self._parse_json(response, allow_errors=True)
            if "access_token" in payload:
                try:
                    return GitHubAccessToken.model_validate(payload)
                except ValidationError as exc:
                    raise DeviceAuthorizationError(
                        f"GitHub returned invalid access-token payload: {exc}"
                    ) from exc
            try:
                error = GitHubOAuthError.model_validate(payload)
            except ValidationError as exc:
                raise DeviceAuthorizationError(
                    f"GitHub returned unexpected OAuth error payload: {exc}"
                ) from exc
            if error.error == "authorization_pending":
                continue
            if error.error == "slow_down":
                interval = max(interval + 5, error.interval or 0)
                continue
            if error.error in {"expired_token", "token_expired"}:
                raise DeviceAuthorizationError(
                    "GitHub device code expired before authorization completed."
                )
            if error.error == "access_denied":
                raise DeviceAuthorizationError("GitHub device authorization was canceled.")
            if error.error == "device_flow_disabled":
                raise DeviceAuthorizationError(
                    "GitHub OAuth app does not have device flow enabled."
                )
            raise DeviceAuthorizationError(
                self._format_error("GitHub device authorization failed", error)
            )

        raise DeviceAuthorizationError(
            "GitHub device code expired before authorization completed."
        )

    def fetch_user_login(self, github_token: str) -> str | None:
        with httpx.Client(timeout=self._request_timeout) as client:
            response = client.get(
                f"{self._github_api_base_url}/user",
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {github_token}",
                    "x-github-api-version": self._api_version,
                },
            )
        if response.status_code >= 400:
            return None
        payload = self._parse_json(response)
        login = payload.get("login")
        return login if isinstance(login, str) and login else None

    def verify_copilot_access(self, github_token: str) -> None:
        with httpx.Client(timeout=self._request_timeout) as client:
            response = client.get(
                f"{self._github_api_base_url}/copilot_internal/v2/token",
                headers=self._copilot_exchange_headers(github_token),
            )
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise DeviceAuthorizationError(
                "GitHub login succeeded, but Copilot token exchange failed: "
                f"{detail}"
            )
        payload = self._parse_json(response)
        if not isinstance(payload.get("token"), str) or not payload.get("token"):
            raise DeviceAuthorizationError(
                "GitHub login succeeded, but Copilot token exchange returned no token."
            )

    def build_stored_token(
        self,
        access_token: GitHubAccessToken,
        *,
        github_login: str | None,
    ) -> StoredGitHubToken:
        return StoredGitHubToken(
            github_token=access_token.access_token,
            token_type=access_token.token_type,
            scope=access_token.scope,
            client_id=self._client_id,
            created_at=datetime.now(tz=UTC),
            github_login=github_login,
        )

    def _copilot_exchange_headers(self, github_token: str) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"token {github_token}",
            "editor-version": f"vscode/{self._editor_version}",
            "editor-plugin-version": self._editor_plugin_version,
            "user-agent": self._user_agent,
            "x-github-api-version": self._api_version,
            "x-vscode-user-agent-library-version": "electron-fetch",
        }

    @staticmethod
    def _format_error(prefix: str, error: GitHubOAuthError) -> str:
        if error.error_description:
            return f"{prefix}: {error.error} ({error.error_description})"
        return f"{prefix}: {error.error}"

    @staticmethod
    def _parse_json(
        response: httpx.Response,
        *,
        allow_errors: bool = False,
    ) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeviceAuthorizationError(
                f"GitHub returned non-JSON response: {response.text}"
            ) from exc
        if not isinstance(payload, dict):
            raise DeviceAuthorizationError(
                f"GitHub returned unexpected response shape: {payload!r}"
            )
        if response.status_code >= 400 and not allow_errors:
            error = None
            try:
                error = GitHubOAuthError.model_validate(payload)
            except ValidationError:
                pass
            if error is not None:
                raise DeviceAuthorizationError(
                    GitHubDeviceFlowClient._format_error(
                        "GitHub request failed",
                        error,
                    )
                )
            raise DeviceAuthorizationError(
                f"GitHub request failed with HTTP {response.status_code}: {response.text}"
            )
        return payload
