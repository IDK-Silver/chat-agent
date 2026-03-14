"""Auth helpers for the native Claude Code proxy."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
DEFAULT_CLAUDE_CODE_OAUTH_SCOPE = "org:create_api_key user:profile user:inference"
DEFAULT_CLAUDE_CODE_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
DEFAULT_CLAUDE_CODE_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
DEFAULT_CLAUDE_CODE_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredClaudeCodeToken(_StrictModel):
    """Persisted Claude Code OAuth token used by the local proxy."""

    version: int = 1
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_at: datetime
    source: Literal[
        "imported_claude_code_credentials",
        "oauth_browser",
        "oauth_refresh",
        "env_override",
    ]
    client_id: str = Field(min_length=1)
    created_at: datetime


class ClaudeCodeCredentialTokens(_StrictModel):
    # Claude Code may add metadata fields that are irrelevant for token refresh.
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(
        min_length=1,
        validation_alias=AliasChoices("access_token", "accessToken"),
    )
    refresh_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("refresh_token", "refreshToken"),
    )
    expires_at_ms: int | float | None = Field(
        default=None,
        validation_alias=AliasChoices("expires_at", "expiresAt"),
    )


class ClaudeCodeOAuthTokens(_StrictModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_in: int | float = Field(gt=0)


class ClaudeCodeBrowserAuthorization(_StrictModel):
    authorization_url: str = Field(min_length=1)
    code_verifier: str = Field(min_length=1)
    state: str = Field(min_length=1)


def default_token_path() -> Path:
    """Return the platform-appropriate default proxy token store path."""

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "chat-agent" / "claude-code-proxy" / "token.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chat-agent"
            / "claude-code-proxy"
            / "token.json"
        )
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "chat-agent" / "claude-code-proxy" / "token.json"


def default_credentials_paths() -> list[Path]:
    """Return likely Claude Code credential file locations."""

    home = Path.home()
    return [
        home / ".claude" / ".credentials.json",
        home / ".claude" / "credentials.json",
    ]


def default_keychain_service_names() -> list[str]:
    """Return likely macOS keychain item names used by Claude Code."""

    return [
        "Claude Code-credentials",
        "Claude-credentials",
    ]


def build_pkce_pair() -> tuple[str, str]:
    """Return a PKCE verifier/challenge pair."""

    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_state_token() -> str:
    """Return a CSRF state token for the browser OAuth flow."""

    return secrets.token_urlsafe(24)


def parse_manual_authorization_code(value: str) -> tuple[str, str]:
    """Parse Anthropic's manual callback format: `code#state`."""

    cleaned = value.strip()
    code, separator, state = cleaned.partition("#")
    if not separator or not code or not state:
        raise ValueError(
            "Authorization code must be pasted as `code#state` from the Anthropic callback page."
        )
    return code.strip(), state.strip()


def resolve_token_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is None:
        return default_token_path()
    return Path(path).expanduser()


def resolve_credentials_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    if path is None:
        return None
    return Path(path).expanduser()


class ClaudeCodeTokenStore:
    """Load and save proxy-managed Claude Code tokens."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StoredClaudeCodeToken | None:
        if not self.path.is_file():
            return None
        try:
            return StoredClaudeCodeToken.model_validate_json(self.path.read_text())
        except (OSError, ValidationError) as exc:
            raise ValueError(
                f"Invalid Claude Code token store at {self.path}: {exc}"
            ) from exc

    def save(self, token: StoredClaudeCodeToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(token.model_dump_json(indent=2))
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)


class ClaudeCodeCredentialLoader:
    """Read Claude Code-installed OAuth credentials without modifying them."""

    def __init__(self, *, path: Path | None = None):
        self._path = path

    def load(self) -> StoredClaudeCodeToken | None:
        candidates = [self._path] if self._path is not None else default_credentials_paths()
        for path in candidates:
            if path is None or not path.is_file():
                continue
            raw = self._read_json(path)
            return self._parse_credentials_payload(
                raw,
                source_name=f"Claude Code credentials at {path}",
            )
        if self._path is None:
            keychain_token = self._load_from_keychain()
            if keychain_token is not None:
                return keychain_token
        return None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            import json

            return json.loads(path.read_text())
        except OSError as exc:
            raise ValueError(f"Failed to read Claude Code credentials at {path}: {exc}") from exc
        except ValueError as exc:
            raise ValueError(
                f"Failed to parse Claude Code credentials at {path}: {exc}"
            ) from exc

    @staticmethod
    def _read_keychain_secret(service_name: str) -> dict[str, Any] | None:
        try:
            completed = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", service_name],
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        secret = completed.stdout.strip()
        if not secret:
            raise ValueError(
                f"Claude Code keychain item {service_name!r} does not contain JSON credentials"
            )
        try:
            import json

            return json.loads(secret)
        except ValueError as exc:
            raise ValueError(
                f"Failed to parse Claude Code keychain item {service_name!r}: {exc}"
            ) from exc

    @staticmethod
    def _extract_oauth_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        candidate = payload.get("claudeAiOauth")
        if isinstance(candidate, dict):
            return candidate
        candidate = payload.get("claude_ai_oauth")
        if isinstance(candidate, dict):
            return candidate
        if "accessToken" in payload or "access_token" in payload:
            return payload
        return None

    def _load_from_keychain(self) -> StoredClaudeCodeToken | None:
        if sys.platform != "darwin":
            return None
        for service_name in default_keychain_service_names():
            raw = self._read_keychain_secret(service_name)
            if raw is None:
                continue
            return self._parse_credentials_payload(
                raw,
                source_name=f"Claude Code keychain item {service_name!r}",
            )
        return None

    @staticmethod
    def _parse_credentials_payload(
        raw: dict[str, Any],
        *,
        source_name: str,
    ) -> StoredClaudeCodeToken:
        oauth_raw = ClaudeCodeCredentialLoader._extract_oauth_payload(raw)
        if oauth_raw is None:
            raise ValueError(f"{source_name} does not contain claudeAiOauth")
        try:
            tokens = ClaudeCodeCredentialTokens.model_validate(oauth_raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid {source_name}: {exc}") from exc
        expires_at = _coerce_expiry(tokens.expires_at_ms)
        if expires_at is None:
            raise ValueError(f"{source_name} does not contain expiresAt")
        return StoredClaudeCodeToken(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=expires_at,
            source="imported_claude_code_credentials",
            client_id=DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID,
            created_at=datetime.now(tz=UTC),
        )


class ClaudeCodeOAuthClient:
    """Run Claude browser OAuth and persist resulting proxy tokens."""

    def __init__(
        self,
        *,
        request_timeout: float,
        client_id: str = DEFAULT_CLAUDE_CODE_OAUTH_CLIENT_ID,
        authorize_url: str = DEFAULT_CLAUDE_CODE_OAUTH_AUTHORIZE_URL,
        token_url: str = DEFAULT_CLAUDE_CODE_OAUTH_TOKEN_URL,
        redirect_uri: str = DEFAULT_CLAUDE_CODE_OAUTH_REDIRECT_URI,
        scope: str = DEFAULT_CLAUDE_CODE_OAUTH_SCOPE,
    ):
        self._request_timeout = request_timeout
        self._client_id = client_id
        self._authorize_url = authorize_url.rstrip("/")
        self._token_url = token_url
        self._redirect_uri = redirect_uri
        self._scope = scope

    def begin_authorization(self) -> ClaudeCodeBrowserAuthorization:
        code_verifier, code_challenge = build_pkce_pair()
        state = build_state_token()
        params = urlencode(
            {
                "code": "true",
                "client_id": self._client_id,
                "response_type": "code",
                "redirect_uri": self._redirect_uri,
                "scope": self._scope,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        return ClaudeCodeBrowserAuthorization(
            authorization_url=f"{self._authorize_url}?{params}",
            code_verifier=code_verifier,
            state=state,
        )

    def exchange_manual_code(
        self,
        manual_code: str,
        *,
        authorization: ClaudeCodeBrowserAuthorization,
    ) -> StoredClaudeCodeToken:
        code, returned_state = parse_manual_authorization_code(manual_code)
        if returned_state != authorization.state:
            raise ValueError("Authorization state mismatch. Restart `claude-code-proxy login`.")

        with httpx.Client(timeout=self._request_timeout) as client:
            response = client.post(
                self._token_url,
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "state": authorization.state,
                    "client_id": self._client_id,
                    "code_verifier": authorization.code_verifier,
                    "redirect_uri": self._redirect_uri,
                },
            )
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise RuntimeError(f"Claude OAuth token exchange failed: {detail}")
        try:
            payload = ClaudeCodeOAuthTokens.model_validate(response.json())
        except ValidationError as exc:
            raise RuntimeError(
                f"Claude OAuth token exchange returned invalid payload: {exc}"
            ) from exc
        return self.build_stored_token(payload)

    def build_stored_token(self, payload: ClaudeCodeOAuthTokens) -> StoredClaudeCodeToken:
        return StoredClaudeCodeToken(
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            expires_at=datetime.now(tz=UTC).replace(microsecond=0)
            + _seconds_to_delta(payload.expires_in),
            source="oauth_browser",
            client_id=self._client_id,
            created_at=datetime.now(tz=UTC).replace(microsecond=0),
        )


def _coerce_expiry(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def _seconds_to_delta(value: int | float):
    from datetime import timedelta

    return timedelta(seconds=float(value))


def is_token_fresh(token: StoredClaudeCodeToken, *, buffer_seconds: int = 60) -> bool:
    return token.expires_at.timestamp() - buffer_seconds > datetime.now(tz=UTC).timestamp()


def normalize_bearer_token(token: str) -> str:
    cleaned = token.strip()
    if cleaned.lower().startswith("bearer "):
        return cleaned[7:].strip()
    return cleaned
