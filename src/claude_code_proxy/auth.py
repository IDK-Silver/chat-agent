"""Auth helpers for the native Claude Code proxy."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Iterator, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
    id: str = Field(min_length=1)
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

    @field_validator("expires_at", "created_at")
    @classmethod
    def _ensure_utc(cls, value: datetime) -> datetime:
        # Externally-produced or hand-edited records may store naive datetimes; assume
        # UTC so sorting by created_at and expires_at.timestamp() never mix naive/aware.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


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
        return root / "chat-agent" / "claude-code-proxy" / "tokens.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chat-agent"
            / "claude-code-proxy"
            / "tokens.json"
        )
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "chat-agent" / "claude-code-proxy" / "tokens.json"


def build_pkce_pair() -> tuple[str, str]:
    """Return a PKCE verifier/challenge pair."""

    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_state_token() -> str:
    """Return a CSRF state token for the browser OAuth flow."""

    return secrets.token_urlsafe(24)


def new_token_id() -> str:
    """Return a short opaque id for a stored token."""

    return secrets.token_urlsafe(8)


def parse_manual_authorization_code(value: str) -> tuple[str, str]:
    """Parse Anthropic's manual callback format: `code#state`."""

    cleaned = value.strip()
    code, separator, state = cleaned.partition("#")
    if not separator or not code or not state:
        raise ValueError(
            "Authorization code must be pasted as `code#state` from the Anthropic callback page."
        )
    return code.strip(), state.strip()


class StoredClaudeCodeTokenStore:
    """Load and save proxy-managed Claude Code tokens (multi-token store)."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_token_path()

    def load_all(self) -> list[StoredClaudeCodeToken]:
        """Return all stored tokens ordered by priority (newest created_at first)."""

        tokens = self._read_records()
        if tokens is None:
            return []
        return sorted(tokens, key=lambda t: t.created_at, reverse=True)

    def save(self, token: StoredClaudeCodeToken) -> None:
        """Append a token to the store, keeping existing entries."""

        with self._exclusive_lock():
            tokens = [t for t in self._read_records() or [] if t.id != token.id]
            tokens.append(token)
            self._write_records(tokens)

    def replace_all(self, tokens: list[StoredClaudeCodeToken]) -> None:
        with self._exclusive_lock():
            self._write_records(tokens)

    def remove(self, token_id: str) -> bool:
        """Drop the token with the given id. Return True if a token was removed."""

        with self._exclusive_lock():
            tokens = self._read_records() or []
            remaining = [t for t in tokens if t.id != token_id]
            if len(remaining) == len(tokens):
                return False
            self._write_records(remaining)
            return True

    def promote(self, token_id: str) -> bool:
        """Move the token with the given id to the front of the priority order.

        Priority is by ``created_at`` (newest first). Promoting means bumping its
        ``created_at`` to just above the current maximum so it sorts first.
        Return True if the token existed.
        """

        with self._exclusive_lock():
            tokens = self._read_records() or []
            target = next((t for t in tokens if t.id == token_id), None)
            if target is None:
                return False
            max_created = max((t.created_at for t in tokens), default=datetime.now(tz=UTC))
            bumped = target.model_copy(
                update={"created_at": max_created.replace(microsecond=0) + _seconds(1)}
            )
            new_tokens = [bumped if t.id == token_id else t for t in tokens]
            self._write_records(new_tokens)
            return True

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Serialize read-modify-write across processes via an advisory file lock.

        Guards against a lost update when serve's in-process token refresh and a
        separate `login` / `tokens` CLI invocation write the store concurrently.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read_records(self) -> list[StoredClaudeCodeToken] | None:
        """Return raw records (no sort). Migrate legacy single-token file on first read."""

        self._migrate_legacy_if_present()
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid Claude Code token store at {self.path}: {exc}") from exc
        if not isinstance(payload, (list, dict)):
            raise ValueError(f"Invalid Claude Code token store at {self.path}: unexpected payload")
        return self._parse_records(payload)

    @staticmethod
    def _parse_records(payload: object) -> list[StoredClaudeCodeToken]:
        """Validate records, skipping any malformed one so it can't poison the store.

        A single bad record (hand-edited, truncated, or written by a build with an
        extra field) must not disable every other stored token and defeat failover.
        """

        items = payload if isinstance(payload, list) else [payload]
        records: list[StoredClaudeCodeToken] = []
        for item in items:
            try:
                records.append(StoredClaudeCodeToken.model_validate(item))
            except ValidationError:
                continue
        return records

    def _migrate_legacy_if_present(self) -> bool:
        """If the pre-multi-token ``token.json`` exists, fold it into ``tokens.json``.

        One-shot: read the legacy single-token file (which sits beside this store's own
        path, never the global default), append it with a fresh id, then remove it.
        """

        legacy = self.path.with_name("token.json")
        if legacy == self.path or not legacy.is_file():
            return False
        try:
            raw = json.loads(legacy.read_text())
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        try:
            token = StoredClaudeCodeToken.model_validate({**raw, "id": new_token_id()})
        except ValidationError:
            return False
        existing: list[StoredClaudeCodeToken] = []
        if self.path.is_file():
            try:
                existing = self._parse_records(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                # Existing store is unreadable/corrupt; don't clobber it while migrating.
                return False
        # Dedupe by access_token so a failed unlink() below cannot duplicate the
        # legacy token on a later re-read (migration would otherwise run again).
        if not any(t.access_token == token.access_token for t in existing):
            existing.append(token)
            self._write_records(existing)
        try:
            legacy.unlink()
        except OSError:
            pass
        return True

    def _write_records(self, tokens: list[StoredClaudeCodeToken]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps([t.model_dump(mode="json") for t in tokens], indent=2))
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)


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
            raise ValueError("Authorization state mismatch. Restart the login flow.")

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
        # Keep sub-second precision so two logins in the same wall-clock second still
        # order newest-first (load_all sorts by created_at).
        now = datetime.now(tz=UTC)
        return StoredClaudeCodeToken(
            id=new_token_id(),
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            expires_at=now + _seconds(payload.expires_in),
            source="oauth_browser",
            client_id=self._client_id,
            created_at=now,
        )


def _seconds(value: int | float) -> timedelta:
    return timedelta(seconds=float(value))


def is_token_fresh(token: StoredClaudeCodeToken, *, buffer_seconds: int = 60) -> bool:
    return token.expires_at.timestamp() - buffer_seconds > datetime.now(tz=UTC).timestamp()


def normalize_bearer_token(token: str) -> str:
    cleaned = token.strip()
    if cleaned.lower().startswith("bearer "):
        return cleaned[7:].strip()
    return cleaned
