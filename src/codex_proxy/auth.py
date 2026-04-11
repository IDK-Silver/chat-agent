"""Auth helpers for the native Codex proxy."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
from typing import Any, Callable, Literal
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Reverse-engineered from:
# https://github.com/insightflo/chatgpt-codex-proxy/blob/main/src/auth.ts
# https://github.com/icebear0828/codex-proxy/blob/main/src/auth/oauth-pkce.ts
DEFAULT_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CODEX_OAUTH_SCOPE = "openid profile email offline_access"
DEFAULT_CODEX_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
DEFAULT_CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CODEX_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredCodexToken(_StrictModel):
    """Persisted Codex OAuth token used by the local proxy."""

    version: int = 1
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    account_id: str = Field(min_length=1)
    expires_at: datetime
    source: Literal[
        "imported_codex_auth",
        "oauth_browser",
        "oauth_refresh",
        "env_override",
    ]
    client_id: str = Field(min_length=1)
    created_at: datetime


class CodexAuthPayload(BaseModel):
    """Subset of the official Codex CLI auth.json payload."""

    auth_mode: str | None = None
    last_refresh: str | int | float | None = None
    tokens: dict[str, Any]

    model_config = ConfigDict(extra="ignore")


class CodexOAuthTokens(_StrictModel):
    """OAuth token payload returned by auth.openai.com."""

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_in: int | float | None = None

    model_config = ConfigDict(extra="ignore")


class CodexBrowserAuthorization(_StrictModel):
    """Browser authorization metadata for the Codex OAuth flow."""

    authorization_url: str = Field(min_length=1)
    code_verifier: str = Field(min_length=1)
    state: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)


def default_token_path() -> Path:
    """Return the platform-appropriate default proxy token store path."""

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "chat-agent" / "codex-proxy" / "token.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chat-agent"
            / "codex-proxy"
            / "token.json"
        )
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "chat-agent" / "codex-proxy" / "token.json"


def default_codex_auth_path() -> Path:
    """Return the default official Codex CLI auth path."""

    return Path.home() / ".codex" / "auth.json"


def resolve_token_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is None:
        return default_token_path()
    return Path(path).expanduser()


def resolve_codex_auth_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is None:
        return default_codex_auth_path()
    return Path(path).expanduser()


def build_pkce_pair() -> tuple[str, str]:
    """Return a PKCE verifier/challenge pair."""

    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_state_token() -> str:
    """Return a CSRF state token for the browser OAuth flow."""

    return secrets.token_urlsafe(24)


def normalize_bearer_token(value: str) -> str:
    cleaned = value.strip()
    if cleaned.lower().startswith("bearer "):
        return cleaned[7:].strip()
    return cleaned


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token is not a JWT")
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        raw = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("failed to decode JWT payload") from exc
    if not isinstance(raw, dict):
        raise ValueError("JWT payload must be an object")
    return raw


def extract_token_expiry(token: str) -> datetime:
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise ValueError("JWT payload missing exp")
    return datetime.fromtimestamp(float(exp), tz=UTC)


def extract_chatgpt_account_id(token: str) -> str:
    payload = _decode_jwt_payload(token)
    auth_info = payload.get("https://api.openai.com/auth")
    if not isinstance(auth_info, dict):
        raise ValueError("JWT payload missing OpenAI auth claim")
    account_id = auth_info.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("JWT payload missing chatgpt_account_id")
    return account_id


def is_token_fresh(token: StoredCodexToken, *, skew_seconds: int = 60) -> bool:
    return token.expires_at.timestamp() - skew_seconds > datetime.now(tz=UTC).timestamp()


def _parse_created_at(value: str | int | float | None) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(tz=UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.now(tz=UTC)


class CodexTokenStore:
    """Load and save proxy-managed Codex tokens."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StoredCodexToken | None:
        if not self.path.is_file():
            return None
        try:
            return StoredCodexToken.model_validate_json(self.path.read_text())
        except (OSError, ValidationError) as exc:
            raise ValueError(f"Invalid Codex token store at {self.path}: {exc}") from exc

    def save(self, token: StoredCodexToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(token.model_dump_json(indent=2))
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)


class CodexAuthLoader:
    """Read official Codex CLI auth state without modifying it."""

    def __init__(self, *, path: Path | None = None):
        self._path = path

    def load(self) -> StoredCodexToken | None:
        path = self._path or default_codex_auth_path()
        if not path.is_file():
            return None
        try:
            payload = CodexAuthPayload.model_validate_json(path.read_text())
        except (OSError, ValidationError, ValueError) as exc:
            raise ValueError(f"Failed to parse Codex auth at {path}: {exc}") from exc

        tokens = payload.tokens
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        account_id = tokens.get("account_id")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError(f"Codex auth at {path} does not contain access_token")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise ValueError(f"Codex auth at {path} has invalid refresh_token")
        if not isinstance(account_id, str) or not account_id:
            account_id = extract_chatgpt_account_id(access_token)

        return StoredCodexToken(
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id,
            expires_at=extract_token_expiry(access_token),
            source="imported_codex_auth",
            client_id=DEFAULT_CODEX_OAUTH_CLIENT_ID,
            created_at=_parse_created_at(payload.last_refresh),
        )


class CodexOAuthClient:
    """Run Codex browser OAuth and persist resulting proxy tokens."""

    def __init__(
        self,
        *,
        request_timeout: float,
        client_id: str = DEFAULT_CODEX_OAUTH_CLIENT_ID,
        authorize_url: str = DEFAULT_CODEX_OAUTH_AUTHORIZE_URL,
        token_url: str = DEFAULT_CODEX_OAUTH_TOKEN_URL,
        redirect_uri: str = DEFAULT_CODEX_OAUTH_REDIRECT_URI,
        scope: str = DEFAULT_CODEX_OAUTH_SCOPE,
    ):
        self._request_timeout = request_timeout
        self._client_id = client_id
        self._authorize_url = authorize_url.rstrip("/")
        self._token_url = token_url
        self._redirect_uri = redirect_uri
        self._scope = scope

    def begin_authorization(self) -> CodexBrowserAuthorization:
        code_verifier, code_challenge = build_pkce_pair()
        state = build_state_token()
        params = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": self._scope,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "state": state,
                "originator": "codex_cli_rs",
            },
            quote_via=quote,
        )
        return CodexBrowserAuthorization(
            authorization_url=f"{self._authorize_url}?{params}",
            code_verifier=code_verifier,
            state=state,
            redirect_uri=self._redirect_uri,
        )

    def exchange_callback_code(
        self,
        code: str,
        *,
        returned_state: str,
        authorization: CodexBrowserAuthorization,
    ) -> StoredCodexToken:
        if returned_state != authorization.state:
            raise ValueError("Authorization state mismatch. Restart `codex-proxy login`.")

        with httpx.Client(timeout=self._request_timeout) as client:
            response = client.post(
                self._token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "code": code,
                    "code_verifier": authorization.code_verifier,
                    "redirect_uri": authorization.redirect_uri,
                },
            )
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise RuntimeError(f"Codex OAuth token exchange failed: {detail}")
        try:
            payload = CodexOAuthTokens.model_validate(response.json())
        except ValidationError as exc:
            raise RuntimeError(
                f"Codex OAuth token exchange returned invalid payload: {exc}"
            ) from exc
        return self.build_stored_token(payload)

    def build_stored_token(self, payload: CodexOAuthTokens) -> StoredCodexToken:
        return StoredCodexToken(
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            account_id=extract_chatgpt_account_id(payload.access_token),
            expires_at=extract_token_expiry(payload.access_token),
            source="oauth_browser",
            client_id=self._client_id,
            created_at=datetime.now(tz=UTC).replace(microsecond=0),
        )


def wait_for_browser_callback(
    authorization: CodexBrowserAuthorization,
    *,
    on_ready: Callable[[], None] | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[str, str]:
    """Wait for the browser OAuth callback and return (code, state)."""

    parsed = urlparse(authorization.redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    callback_path = parsed.path or "/"
    results: queue.Queue[tuple[str | None, str | None, str | None]] = queue.Queue(maxsize=1)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_url = urlparse(self.path)
            if request_url.path != callback_path:
                self.send_error(404, "Not found")
                return

            params = parse_qs(request_url.query)
            code = _first_query_value(params, "code")
            state = _first_query_value(params, "state")
            error = _first_query_value(params, "error")
            error_description = _first_query_value(params, "error_description")

            if error:
                results.put((None, None, error_description or error))
                self._write_html(_callback_html(success=False, message=error_description or error))
                return

            if not code or not state:
                results.put((None, None, "Missing code or state parameter"))
                self._write_html(
                    _callback_html(
                        success=False,
                        message="Missing code or state parameter.",
                    )
                )
                return

            results.put((code, state, None))
            self._write_html(_callback_html(success=True, message=None))

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _write_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    try:
        server = HTTPServer((host, port), _Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to start OAuth callback server on {host}:{port}: {exc}"
        ) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if on_ready is not None:
        on_ready()
    try:
        code, state, error = results.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise RuntimeError("Timed out waiting for browser OAuth callback.") from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    if error:
        raise RuntimeError(f"Browser OAuth failed: {error}")
    assert code is not None
    assert state is not None
    return code, state


def _first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _callback_html(*, success: bool, message: str | None) -> str:
    title = "Authentication Successful" if success else "Authentication Failed"
    heading = "Authentication Successful" if success else "Authentication Failed"
    detail = (
        "You can close this window and return to the terminal."
        if success
        else (message or "Authentication failed.")
    )
    color = "#10a37f" if success else "#d14343"
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{title}</title>
  </head>
  <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #111827;">
    <div style="text-align: center; color: #f3f4f6; max-width: 480px; padding: 24px;">
      <h1 style="color: {color};">{heading}</h1>
      <p>{detail}</p>
    </div>
  </body>
</html>
"""
